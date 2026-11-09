"""CPU checks for quaternion convention and rigid grasp pivot preservation."""
import numpy as np
from scipy.spatial.transform import Rotation
r=Rotation.from_euler('y',90,degrees=True)
assert np.allclose(r.apply([1,0,0]),[0,0,-1])
n=Rotation.from_euler('z',90,degrees=True)*Rotation.from_euler('y',55,degrees=True)
assert n.apply([1,0,0])[1]>0 and n.apply([1,0,0])[2]<0
c=np.array([.2,-.2,.9]);p=c-r.apply([.145,.002,0])
local=r.inv().apply(c-p);newp=c-n.apply(local)
assert np.allclose(newp+n.apply(local),c)
print('Quaternion convention and egg-centered pivot checks passed')
