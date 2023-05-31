from torch.utils.tensorboard import SummaryWriter
from data.dataset import get_loader 
from utils.metric import compute_metrics

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torch.backends.cudnn as cudnn
import SimpleITK as sitk
from monai.optimizers import WarmupCosineSchedule
from monai.networks.nets import UNet
from monai import transforms as tfs
from monai.inferers import sliding_window_inference
from monai.losses import DiceLoss
from monai.metrics import DiceMetric
from torchmetrics.functional import dice
from monai.data import decollate_batch

import argparse
import os
import subprocess
import time

# parameters
parser = argparse.ArgumentParser(description='Medical 3D Segmentation')
parser.add_argument('--resume_path', default=None, help='resume path')
parser.add_argument('--epoch', default=200, help='training epoch')
parser.add_argument('--bs', default=1, help='batch size')
parser.add_argument('--lr', default=0.01, help="learning rate")
parser.add_argument('--l1', default=1, help="lambda1 for reconstruction loss")
parser.add_argument('--board', default='./runs', help="tensorboard path")
parser.add_argument('--save_path', default='./checkpoints/', help="save path")
parser.add_argument('--output_path', default='./output/', help="save epoch")
parser.add_argument('--dp', default=False, help="whether to use ddp or not")
parser.add_argument('--classes', default=1, help="number of classes")
args = parser.parse_args()

# set GPU
# os.environ['CUDA_VISIBLE_DEVICES'] = '1'

if args.dp:
    device_ids = [0,1]
else:
    os.environ['CUDA_VISIBLE_DEVICES'] = str(np.argmax([int(x.split()[2]) for x in subprocess.Popen(
        "nvidia-smi -q -d Memory | grep -A4 GPU | grep Free", shell=True, stdout=subprocess.PIPE).stdout.readlines()]))
    cudnn.benchmark = False
    device_ids = [1]

# loading datasets
batch_size = int(args.bs)
ct_path1 = "/home/ubuntu/disk1/TLX/datasets/seg_demo/images/"
mask_path1 = "/home/ubuntu/disk1/TLX/datasets/seg_demo/labels/"
ct_path = [ct_path1]
label_path = [mask_path1]

train_loader, val_loader = get_loader(batch_size, label_path, ct_path, mode='train')

model = UNet(
    spatial_dims=3,
    in_channels=1,
    out_channels=int(args.classes),
    channels=(16, 32, 64, 128, 256),
    strides=(2, 2, 2, 2),
    num_res_units=2,
)

if args.dp:
    print("Using multi GPUs...")
    device = torch.device("cuda:0")
    model = torch.nn.DataParallel(model, device_ids=device_ids)
    model = model.to(device)
else:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
# device = torch.device("cpu")

optimizer = optim.Adam(model.parameters(), lr=float(args.lr), weight_decay=1e-5)
scheduler = WarmupCosineSchedule(optimizer, 
                                warmup_steps=10*len(train_loader), 
                                t_total=int(args.epoch)*len(train_loader))

# loading checkpoints
if args.resume_path is not None:
    print("Continue training...")
    checkpoint = torch.load(args.resume_path, map_location=torch.device("cpu"))
    if args.dp:
        model.module.load_state_dict(checkpoint['net'])
    else:
        model.load_state_dict(checkpoint['net'])
    epoch_start = checkpoint['epoch']
    optimizer.load_state_dict(checkpoint['optimizer'])
    optimizer.param_groups[0]['lr'] = float(args.lr)
    best_dice_metric = checkpoint['best_dice']
    best_dice_epoch = checkpoint['best_dice_epoch']
else:
    print("Beginning epoch...")
    best_dice_metric = None
    epoch_start = 0

# create needed folders
if os.path.exists(args.board) == False:
    os.makedirs(args.board)
if os.path.exists(args.output_path) == False:
    os.makedirs(args.output_path)
output_path = args.output_path
if os.path.exists(args.output_path+'/val/') == False:
    os.makedirs(args.output_path+'/val/')
val_output_path = args.output_path+'/val/'
if os.path.exists(args.save_path) == False:
    os.makedirs(args.save_path)
save_path = args.save_path

# tensorboard
writer = SummaryWriter(args.board)

start = time.time()
val_interval = 1
save_interval = 25
if best_dice_metric == None:
    best_dice_metric = -1
    best_dice_epoch = 0
    
loss_fn = DiceLoss(smooth_nr=0, smooth_dr=1e-5, squared_pred=True, to_onehot_y=False, sigmoid=True)
post_trans = tfs.Compose([tfs.Activations(sigmoid=True), tfs.AsDiscrete(threshold=0.5)])

# init optimizer for scheduler
optimizer.zero_grad()
optimizer.step()

# training loop
for epoch in range(epoch_start, epoch_start+int(args.epoch)):
    running_loss = 0.0
    
    model.train()
    scheduler.step()
    for step, sample in enumerate(train_loader):
        with torch.autograd.set_detect_anomaly(True):
            label = sample['label']
            ct = sample['volume']
            label = label.float().to(device)
            ct = ct.float().to(device)
            name = sample['name']

            seg = model(ct)
            seg_loss = int(args.l1)*loss_fn(seg, label)
            loss = seg_loss

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            running_loss += loss.item()

    writer.add_scalar('average loss: {:%.4f}', running_loss/len(train_loader), epoch+1)
    print('epoch %d average loss: %.4f' % (epoch+1, running_loss/len(train_loader)))

    # validation
    if (epoch + 1) % val_interval == 0:
        # reset metrics for each validation
        # dice_metric.reset()
        dice_metric = 0
        model.eval()
        for step, val_sample in enumerate(val_loader):
            with torch.no_grad():
                val_label, val_ct = val_sample["label"].float().cuda(), val_sample["volume"].float().cuda()
                val_name = val_sample['name']
                
                # segmentation
                val_seg = sliding_window_inference(val_ct,(96,96,96),4,model,overlap=0.8)
                val_seg = [post_trans(val_pred_tensor) for val_pred_tensor in val_seg][0].unsqueeze(0)
                dice_metric = dice_metric + dice(val_seg[0], val_label.int())

                # save validation results for visualization
                if not os.path.exists(val_output_path+str(epoch+1)):
                    os.makedirs(val_output_path+str(epoch+1))
                for idx in range(val_seg.shape[0]):
                    res_vol = val_seg.float().cpu().numpy()
                    pred = res_vol[idx].squeeze(0)
                    save_volume = sitk.GetImageFromArray(pred)
                    sitk.WriteImage(save_volume, val_output_path+str(epoch+1)+"/"+val_name[idx]+".nii.gz")
        
        mean_dice = dice_metric/len(val_loader)

        print("val dice:{:.4f}".format(mean_dice))
        writer.add_scalar('val dice:{:%.4f}',mean_dice,epoch + 1)

        if mean_dice > best_dice_metric:
            best_dice_metric = mean_dice
            best_dice_epoch = epoch + 1
            torch.save(model.state_dict(), "best_dice_model.pth")
            print("saved new best dice model")

        print(
            "current epoch: {} current mean dice: {:.4f} best mean dice: {:.4f} at epoch {}".format(
            epoch + 1, mean_dice, best_dice_metric, best_dice_epoch)   
        )
    
    # save checkpoints
    if args.dp:
        state = {
            'net': model.module.state_dict(),
            'epoch': epoch + 1,
            'optimizer': optimizer.state_dict(),
            'best_dice': best_dice_metric,
            'best_dice_epoch': best_dice_epoch,
        }
    else:
        state = {
            'net': model.state_dict(),
            'epoch': epoch + 1,
            'optimizer': optimizer.state_dict(),
            'best_dice': best_dice_metric,
            'best_dice_epoch': best_dice_epoch,
        }

    if (epoch+1) % save_interval == 0:
        torch.save(state, f'{save_path}/{epoch + 1}.pth') # save model and parameters
        print('Saving epoch %d model ...' % (epoch + 1))

training_time = time.time() - start
writer.close()
print('Finished Training')
print('Training time: {:.4f} seconds'.format(training_time))