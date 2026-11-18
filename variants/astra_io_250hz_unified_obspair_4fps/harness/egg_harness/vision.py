import ast
import base64
import json
import time
from pathlib import Path
from openai import AzureOpenAI
from PIL import Image
from .storage import check_configured_storage

def failure_record(error, seconds):
 record={'seconds':seconds,'type':type(error).__name__,'message':str(error)}
 status=getattr(error,'status_code',None)
 if isinstance(status,int):record['http_status']=status
 request_id=getattr(error,'request_id',None)
 if isinstance(request_id,str):record['request_id']=request_id
 response=getattr(error,'response',None)
 if response is not None:
  headers={key:response.headers[key] for key in ('x-request-id','apim-request-id','x-ms-request-id','retry-after') if key in response.headers}
  if headers:record['diagnostic_headers']=headers
 return record

def load_credentials(path):
  tree=ast.parse(Path(path).read_text())
  calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='AzureOpenAI']
  if len(calls)!=1:raise ValueError('Expected one Azure constructor')
  return {k.arg:ast.literal_eval(k.value) for k in calls[0].keywords}

class Vision:
 def __init__(self,config,trace):
  kw=load_credentials(config['credentials'])
  self.client=AzureOpenAI(**kw,max_retries=0,timeout=config['request_timeout'])
  self.config=config;self.trace=Path(trace);self.trace.mkdir(parents=True,exist_ok=False);self.calls=0
 def ask(self,question,images,system=None):
  check_configured_storage()
  self.calls+=1;stem=self.trace/f'{self.calls:04d}'
  content=[{'type':'text','text':question}]
  manifest=[]
  for name,path in images.items():
   image=Image.open(path);manifest.append({'name':name,'path':str(path),'size':image.size})
   content.extend([{'type':'text','text':name},{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(Path(path).read_bytes()).decode(),'detail':'high'}}])
  req={'model':self.config['model'],'messages':[{'role':'system','content':system or 'You are a robot visual perception module. Use only the provided images and robot-derived context. Return precise JSON. Report uncertainty rather than inventing occluded coordinates.'},{'role':'user','content':content}],'reasoning_effort':self.config['reasoning_effort'],'response_format':{'type':'json_object'}}
  stem.with_suffix('.request.json').write_text(json.dumps({'system':req['messages'][0]['content'],'question':question,'images':manifest,'model':req['model'],'reasoning_effort':req['reasoning_effort']}))
  start=time.monotonic()
  try:
   r=self.client.chat.completions.create(**req)
   stem.with_suffix('.response.json').write_text(r.model_dump_json(indent=2))
   stem.with_suffix('.cost.json').write_text(json.dumps({'seconds':time.monotonic()-start,'usage':r.usage.model_dump(),'model':req['model']}))
   if r.choices[0].finish_reason!='stop':raise RuntimeError('Incomplete visual response')
   return json.loads(r.choices[0].message.content)
  except BaseException as e:
   stem.with_suffix('.failure.json').write_text(json.dumps(failure_record(e,time.monotonic()-start)))
   raise
