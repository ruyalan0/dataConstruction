"""Keep support identifiers without copying credentials or arbitrary headers."""
import json
import httpx2
from openai import InternalServerError, APITimeoutError
from egg_harness.vision import failure_record

request=httpx2.Request('POST','https://example.invalid/chat',headers={'authorization':'secret-request'})
response=httpx2.Response(500,request=request,headers={
 'x-request-id':'request-123','apim-request-id':'gateway-456',
 'retry-after':'7','set-cookie':'secret-cookie','authorization':'secret-response'})
error=InternalServerError('Internal server error',response=response,body={'error':{'code':'-4307'}})
record=failure_record(error,3.5)
assert record['http_status']==500 and record['request_id']=='request-123'
assert record['diagnostic_headers']=={'x-request-id':'request-123','apim-request-id':'gateway-456','retry-after':'7'}
assert 'secret' not in json.dumps(record)
timeout=failure_record(APITimeoutError(request=request),300)
assert timeout['type']=='APITimeoutError' and 'http_status' not in timeout
assert 'diagnostic_headers' not in timeout and 'secret' not in json.dumps(timeout)
print('HTTP status/request identifiers retained; credentials and unrelated headers excluded')
