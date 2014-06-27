# This file is part of DQXServer - (C) Copyright 2014, Paul Vauterin, Ben Jeffery, Alistair Miles <info@cggh.org>
# This program is free software licensed under the GNU Affero General Public License.
# You can find a copy of this license in LICENSE in the top directory of the source code or at <http://opensource.org/licenses/AGPL-3.0>


import simplejson
import DQXbase64
import MySQLdb
import config
import time


LogRequests = False


# Enumerates types of actions that can be done on a database entity
class DbOperationType:
    read = 1
    write = 2


# Encapsulates an operation that is done on a database entity
class DbOperation:

    def __init__(self, operationType, databaseName, tableName=None, columnName=None):
        if (databaseName is None) or (databaseName == ''):
            databaseName = config.DB
        self.operationType = operationType
        self.databaseName = databaseName
        self.tableName = tableName
        self.columnName = columnName

    def IsModify(self):
        return self.operationType == DbOperationType.write

    def OnDatabase(self, databaseName):
        return self.databaseName == databaseName

    def OnTable(self, tableName):
        return self.tableName == tableName

    def OnColumn(self, columnName):
        return self.columnName == columnName

    def __str__(self):
        st = ''
        if (self.operationType == DbOperationType.read):
            st += 'Read'
        if (self.operationType == DbOperationType.write):
            st += 'Write'
        st += ':'
        st += self.databaseName
        if self.tableName is not None:
            st += ':' + self.tableName
        if self.columnName is not None:
            st += ':' + self.columnName
        return st


# Encapsulates a read operation that is done on a database entity
class DbOperationRead(DbOperation):
    def __init__(self, databaseName, tableName=None, columnName=None):
        DbOperation.__init__(self, DbOperationType.read, databaseName, tableName, columnName)


# Encapsulates a write operation that is done on a database entity
class DbOperationWrite(DbOperation):
    def __init__(self, databaseName, tableName=None, columnName=None):
        DbOperation.__init__(self, DbOperationType.write, databaseName, tableName, columnName)


# Encapsulates the result of an authorisation request on a database operation
class DbAuthorization:
    def __init__(self, granted, reason=None):
        self.granted = granted
        if reason is None:
            if not granted:
                reason = 'Insufficient privileges to perform this action.'
            else:
                reason = ''
        self.reason = reason
    def IsGranted(self):
        return self.granted
    def __str__(self):
        return self.reason
    def __nonzero__(self):
        return self.granted
    def __bool__(self):
        return self.granted


# Define a custom credential handler here by defining function taking a DbOperation and a CredentialInformation
# returning a DbAuthorization instance
DbCredentialVerifier = None


class CredentialException(Exception):
    def __init__(self, message):
        Exception.__init__(self, message)

class CredentialDatabaseException(CredentialException):
    def __init__(self, operation, auth):
        st = str(auth) + " \n\n[" + str(operation) + ']'
        CredentialException.__init__(self, st)



# Encapsulates information about the credentials a user has
class CredentialInformation:
    def __init__(self):
        self.clientaddress = None
        self.userid = 'anonymous'
        self.groupids = []

    def ParseFromReturnData(self, requestData):
        if ('isRunningLocal' in requestData) and (requestData['isRunningLocal']):
            self.userid = 'local'
            return

        if 'environ' not in requestData:
            raise Exception('Data does not contain environment information')
        environ = requestData['environ']
        #print('ENV:'+str(environ))

        if 'REMOTE_ADDR' in environ:
            self.clientaddress = environ['REMOTE_ADDR']
        if 'REMOTE_USER' in environ:
            self.userid = environ['REMOTE_USER']
        if 'HTTP_CAS_MEMBEROF' in environ:
            cas_memberof = environ['HTTP_CAS_MEMBEROF'].strip('[]')
            for groupStr in cas_memberof.split(';'):
                groupStr = groupStr.strip(' ')
                groupPath = []
                for tokenStr in groupStr.split(','):
                    tokenStr = tokenStr.strip(' ')
                    tokenid = tokenStr.split('=')[0]
                    tokencontent = tokenStr.split('=')[1]
                    if (tokenid == 'cn') or (tokenid == 'ou') or (tokenid == 'dc'):
                        groupPath.append(tokencontent)
                self.groupids.append('.'.join(groupPath))


    # operation is of type DbOperation
    def CanDo(self, operation):
        if DbCredentialVerifier is not None:
            auth = DbCredentialVerifier(self, operation)
            return auth.IsGranted()
        else:
            return True

    # operation is of type DbOperation. raises an exception of not authorised
    def VerifyCanDo(self, operation):
        if DbCredentialVerifier is not None:
            auth = DbCredentialVerifier(self, operation)
            if not(auth.IsGranted()):
                raise CredentialDatabaseException(operation, auth)

    def GetAuthenticationInfo(self):
        str = ''
        str += 'USER=' + self.userid
        str += ';CLIENTADDRESS=' + self.clientaddress
        str += ';GROUPS=' + ','.join(self.groupids)
        return str

    def GetUserId(self):
        return self.userid

# Create a credential info instance from a DQXServer request data environment
def ParseCredentialInfo(requestData):
    cred = CredentialInformation()
    cred.ParseFromReturnData(requestData)
    return cred

def CreateOpenDatabaseArguments():
    args = {
        'host': config.DBSRV,
        'charset': 'utf8',
    }
    try:
        if (len(config.DBUSER) > 0):
            args['user'] = config.DBUSER
            try:
                if len(config.DBPASS) > 0:
                    args['passwd'] = config.DBPASS #try to add password
            except:
                pass
    except:
        args['read_default_file'] = '~/.my.cnf'

    return args

def OpenDatabase(credInfo, database=None, **kwargs):
    if (database is None) or (database == ''):
        database = config.DB
    credInfo.VerifyCanDo(DbOperationRead(database))

    args = CreateOpenDatabaseArguments()
    args['db'] = database
    args.update(kwargs)
    return MySQLdb.connect(**args)

def OpenNoDatabase(credInfo, **kwargs):
    args = CreateOpenDatabaseArguments()
    args.update(kwargs)
    return MySQLdb.connect(**args)

class Timeout(Exception):
    pass

def execute_with_timeout_detection(cur, timeout, query, params):
    t = time.time()
    try:
        return cur.execute(query, params)
    except MySQLdb.OperationalError as e:
        if e[0] == 2013: #Check specific error code (Lost connection)
            #As the MYSQL API doesn't tell us this is a timeout or not we guess based on the fact that the exception was raised just when we expect it to.... yeah I know.
            duration = (time.time() - t)
            #Give 50ms grace in either dir
            if (duration > timeout - 0.05) and (duration < timeout + 0.05):
                raise Timeout()
        raise e

def ToSafeIdentifier(st):
    st = str(st)
    removelist=['"', "'", ';', '(', ')', '`']
    for it in removelist:
        st = st.replace(it, "")
    return st


def DBCOLESC(arg):
    return '`'+ToSafeIdentifier(arg)+'`'

def DBTBESC(arg):
    return '`'+ToSafeIdentifier(arg)+'`'

def DBDBESC(arg):
    return '`'+ToSafeIdentifier(arg)+'`'

#parse column encoding information
def ParseColumnEncoding(columnstr):
    mycolumns=[]
    for colstr in columnstr.split('~'):
        mycolumns.append( { 'Encoding':colstr[0:2], 'Name':ToSafeIdentifier(colstr[2:]) } )
    return mycolumns


#A whereclause encapsulates the where statement of a single table sql query
class WhereClause:
    def __init__(self):
        self.query = None #this contains a tree of statements
        self.ParameterPlaceHolder = "?" #determines what is the placeholder for a parameter to be put in an sql where clause string

    #Decodes an url compatible encoded query into the statement tree
    def Decode(self, encodedstr):
        decodedstr = DQXbase64.b64decode_var2(encodedstr)
        self.query = simplejson.loads(decodedstr)
        pass

    #Creates an SQL where clause string out of the statement tree
    def CreateSelectStatement(self):
        self.querystring = '' #will hold the fully filled in standalone where clause string (do not use this if sql injection is an issue!)
        self.querystring_params = '' #will hold the parametrised where clause string
        self.queryparams = [] #will hold a list of parameter values
        self._CreateSelectStatementSub(self.query)

    def _CreateSelectStatementSub_Compound(self, statm):
        if not(statm['Tpe'] in ['AND', 'OR']):
            raise Exception("Invalid compound statement {0}".format(statm['Tpe']))
        first = True
        for comp in statm['Components']:
            if not first:
                self.querystring += " "+statm['Tpe']+" "
                self.querystring_params += " "+statm['Tpe']+" "
            self.querystring += "("
            self.querystring_params += "("
            self._CreateSelectStatementSub(comp)
            self.querystring += ")"
            self.querystring_params += ")"
            first = False

    def _CreateSelectStatementSub_Comparison(self, statm):
        #TODO: check that statm['ColName'] corresponds to a valid column name in the table (to avoid SQL injection)
        if not(statm['Tpe'] in ['=', '<>', '<', '>', '<=', '>=', '!=', 'LIKE', 'CONTAINS', 'NOTCONTAINS', 'STARTSWITH', 'ISPRESENT', 'ISABSENT', '=FIELD', '<>FIELD', '<FIELD', '>FIELD', 'between', 'ISEMPTYSTR', 'ISNOTEMPTYSTR']):
            raise Exception("Invalid comparison statement {0}".format(statm['Tpe']))

        processed = False


        if statm['Tpe'] == 'ISPRESENT':
            processed = True
            st = '{0} IS NOT NULL'.format(DBCOLESC(statm['ColName']))
            self.querystring += st
            self.querystring_params += st

        if statm['Tpe'] == 'ISABSENT':
            processed = True
            st = '{0} IS NULL'.format(DBCOLESC(statm['ColName']))
            self.querystring += st
            self.querystring_params += st

        if statm['Tpe'] == 'ISEMPTYSTR':
            processed = True
            st = '{0}=""'.format(DBCOLESC(statm['ColName']))
            self.querystring += st
            self.querystring_params += st

        if statm['Tpe'] == 'ISNOTEMPTYSTR':
            processed = True
            st = '{0}<>""'.format(DBCOLESC(statm['ColName']))
            self.querystring += st
            self.querystring_params += st

        if statm['Tpe'] == '=FIELD':
            processed = True
            st = '{0}={1}'.format(
                DBCOLESC(statm['ColName']),
                DBCOLESC(statm['ColName2'])
            )
            self.querystring += st
            self.querystring_params += st

        if statm['Tpe'] == '<>FIELD':
            processed = True
            st = '{0}<>{1}'.format(
                DBCOLESC(statm['ColName']),
                DBCOLESC(statm['ColName2'])
            )
            self.querystring += st
            self.querystring_params += st

        if (statm['Tpe'] == '<FIELD') or (statm['Tpe'] == '>FIELD'):
            processed = True
            operatorstr = statm['Tpe'].split('FIELD')[0]
            self.querystring += '{0} {4} {1} * {2} + {3}'.format(
                DBCOLESC(statm['ColName']),
                ToSafeIdentifier(statm['Factor']),
                DBCOLESC(statm['ColName2']),
                ToSafeIdentifier(statm['Offset']),
                operatorstr)
            self.querystring_params += '{0} {4} {1} * {2} + {3}'.format(
                DBCOLESC(statm['ColName']),
                self.ParameterPlaceHolder,
                DBCOLESC(statm['ColName2']),
                self.ParameterPlaceHolder,
                operatorstr)
            self.queryparams.append(ToSafeIdentifier(statm['Factor']))
            self.queryparams.append(ToSafeIdentifier(statm['Offset']))

        if statm['Tpe'] == 'between':
            processed = True
            self.querystring += DBCOLESC(statm['ColName'])+' between '+ToSafeIdentifier(statm["CompValueMin"])+' and '+ToSafeIdentifier(statm["CompValueMax"])
            self.querystring_params += '{0} between {1} and {1}'.format(DBCOLESC(statm['ColName']), self.ParameterPlaceHolder)
            self.queryparams.append(ToSafeIdentifier(statm["CompValueMin"]))
            self.queryparams.append(ToSafeIdentifier(statm["CompValueMax"]))

        if not(processed):
            decoval = statm['CompValue']
            operatorstr = statm['Tpe']
            if operatorstr == 'CONTAINS':
                operatorstr = 'LIKE'
                decoval = '%{0}%'.format(decoval)
            if operatorstr == 'NOTCONTAINS':
                operatorstr = 'NOT LIKE'
                decoval = '%{0}%'.format(decoval)
            if operatorstr == 'STARTSWITH':
                operatorstr = 'LIKE'
                decoval = '{0}%'.format(decoval)
            self.querystring += DBCOLESC(statm['ColName']) + ' '+ToSafeIdentifier(operatorstr)+' '
            self.querystring_params += '{0} {1} {2}'.format(
                DBCOLESC(statm['ColName']),
                ToSafeIdentifier(operatorstr),
                self.ParameterPlaceHolder)
            needquotes = (type(decoval) is not float) and (type(decoval) is not int)
            if needquotes:
                self.querystring += "'"
                decoval = decoval.replace("'", "")
            else:
                decoval = ToSafeIdentifier(decoval)
            self.querystring += str(decoval)
            if needquotes:
                self.querystring += "'"
            self.queryparams.append(decoval)

    def _CreateSelectStatementSub(self, statm):
        if statm['Tpe'] == '':
            return #trivial query
        self.querystring += "("
        self.querystring_params += "("
        if (statm['Tpe'] == 'AND') or (statm['Tpe'] == 'OR'):
            self._CreateSelectStatementSub_Compound(statm)
        else:
            self._CreateSelectStatementSub_Comparison(statm)
        self.querystring += ")"
        self.querystring_params += ")"





#unpacks an encoded 'order by' statement into an SQL statement
def CreateOrderByStatement(orderstr,reverse=False):
    if (len(orderstr) ==0) or orderstr == 'null':
        return "NULL"
    dirstr = ""
    if reverse: dirstr=" DESC"
    #note the following sql if construct is used to make sure that sorting always puts absent values at the end, which is what we want

    ### !!! todo: make this choice dependent on client
    # option 1 = better, slower (absent appear beneath)
    # opten 2 = sloppier, a lot faster
#    return ', '.join( [ "IF(ISNULL({0}),1,0),{0}{1}".format(DBCOLESC(field),dirstr) for field in orderstr.split('~') ] )
    return ', '.join( [ "{0}{1}".format(DBCOLESC(field), dirstr) for field in orderstr.split('~') ] )
