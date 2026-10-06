import numpy
import sklearn
import torch

assert torch.tensor([1.0, 2.0]).sum().item() == 3.0
assert numpy.__version__ and sklearn.__version__
print("OK")
