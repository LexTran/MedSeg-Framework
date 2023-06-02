# MedSeg-Framework
This repository provides an universal pipeline for medical image segmentation with the support of Pytorch &amp; MONAI.

## Requirement

Here we give the environment tested on our device, but we havn't tried on different versions.
- python == 3.10.4
- pytorch == 1.12.1
- cudatoolkit == 11.6.0
- tensorboard == 2.8.0
- torchmetrics == 0.11.2
- pillow == 9.2.0
- opencv-python == 4.7.0.68
- monai == 1.1.0
- simpleitk == 2.2.0
- thop == 0.1.1.post2209072238

## Training

To train your own network, you need to create a model file under `\network` and use it in `train.py`

The command to run training is as follow:
```
python train.py --epoch=100 --lr=0.01 --board=<where to put your tensorboard log> --save_path=<where to save your model> --output_path=<where to save your results for visualization> --dp=True --classes=1 --data_path=<your data path> --mask_path=<your label path>
```
in this case, `dp` decides whether to use data parallel to support multi-gpus and `classes` decides how many classes to segment.

# Multi-label segmentaion

Our framework supports multi-organ segmentaion, which in many cases may encounter with multiple label files. We have written a script to convert multiple labels into one label, you can find it under `\utils`, named `label_convert.py`.

# Tips

We have provided many useful tools to help you perform data format convertion such as, `mhd -> nii`, `ima -> nii`, `nii -> stl`. Those tools are put under `utils`, have fun with them your way.

## To Do
- [x] Add Multi-task segmentation
- [x] Convert multi labels into one label
