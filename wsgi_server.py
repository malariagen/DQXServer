from urlparse import parse_qs
import importlib
import simplejson
import os

import DQXUtils
import DQXDbTools
import responders
from responders import uploadfile

# import customresponders

# Try to import all custom modules
customRespondersPath = os.path.join(os.path.dirname(__file__), 'customresponders')
for dirname in os.listdir(customRespondersPath):
    tryModulePath = os.path.join(customRespondersPath, dirname)
    if os.path.isdir(tryModulePath):
        importlib.import_module('customresponders.' + dirname)


def application(environ, start_response):
    if environ['PATH_INFO'] == '/favicon.ico':
        DQXUtils.LogServer('404 for favicon')
        status = '404'
        start_response(status, [])
        yield ''
        return
    returndata = dict((k,v[0]) for k,v in parse_qs(environ['QUERY_STRING']).items())
    if 'datatype' not in returndata:
        DQXUtils.LogServer('--> request does not contain datatype')
        start_response('404 NOT FOUND', [('Content-Type', 'text/plain')])
        yield 'Not found: request does not contain datatype'
        return

    request_type = returndata['datatype']

    tm = DQXUtils.Timer()

    if request_type == 'custom':
        request_custommodule = returndata['respmodule']
        request_customid = returndata['respid']
        responder = importlib.import_module('customresponders.' + request_custommodule + '.' + request_customid)
    else:
        try:
            #Fetch the handler by request type, using some introspection magic in responders/__init__.py
            responder = getattr(responders, request_type)
        except AttributeError:
            raise Exception("Unknown request {0}".format(request_type))


    returndata['environ'] = environ
    try:
        response = responder.response(returndata)
    except DQXDbTools.CredentialException as e:
        print('CREDENTIAL EXCEPTION: '+str(e))
        response = returndata
        response['Error'] = 'Credential problem: ' + str(e)

    #Check for a custom response (eg in downloadtable)
    if 'handler' in dir(responder):
        for item in responder.handler(start_response, response):
            yield item

    else:
    #Default is to respond with JSON
        del response['environ']
        response = simplejson.dumps(response, use_decimal=True)
        status = '200 OK'
        response_headers = [('Content-type', 'application/json'),
                            ('Content-Length', str(len(response)))]
        start_response(status, response_headers)
        yield response

    DQXUtils.LogServer('Responded to {0} in wall={1}s cpu={2}s'.format(request_type, tm.Elapsed(),tm.ElapsedCPU()))
