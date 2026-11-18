"""Regression: native max_count is capped by num_envs, including large requests."""
import ast
from pathlib import Path
from types import SimpleNamespace
source=ast.parse(Path('scripts/serve.py').read_text())
body=[]
for node in ast.walk(source):
    if isinstance(node,ast.For) and isinstance(node.iter,ast.Call) and isinstance(node.iter.func,ast.Name) and node.iter.func.id=='range' and 'seed_index' in ast.unparse(node.iter):body.append(node)
assert len(body)==1
class Seeds:
    def __init__(self):self.index=0
    def get_seeds(self,max_count):
        if self.index>=20:return None
        result=[self.index];self.index+=1;return result
for target in [0,1,10,19]:
    scope={'env':SimpleNamespace(seed_manager=Seeds()),'args':SimpleNamespace(seed_index=target,task='fill_egg_holder')}
    exec(compile(ast.Module(body=body,type_ignores=[]),'selection','exec'),scope)
    assert scope['seeds']==[target]
print('Native single-environment seed selection passed for 0,1,10,19')
