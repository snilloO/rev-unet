import torch.utils.data as data
import torch
import h5py
import numpy as np
import random
import cv2
import os
from torch.nn import functional as F

from data.utils import crop_or_pad_slice_to_size

aug_options = ['flip','rot','trans','crop','elastic','intensity']
#stack functions for collate_fn
#Notice: all dicts need have same keys and all lists should have same length
def stack_dicts(dicts):
    if len(dicts)==0:
        return None
    res = {}
    for k in dicts[0].keys():
        res[k] = [obj[k] for obj in dicts]
    return res

def stack_list(lists):
    if len(lists)==0:
        return None
    res = list(range(len(lists[0])))
    for k in range(len(lists[0])):
        res[k] = torch.stack([obj[k] for obj in lists])
    return res
def rand(item):
    try:
        tmp=[]
        for i in item:
            tmp.append(random.uniform(-i,i))
    except:
        if random.random()<0.5:
            return random.uniform(-i,i)
        else:
            return 0
    finally:
        return tuple(tmp)   
def translate_3d(src,mask,trans,z_enable=True):
    h,w,d = src.shape[:3]
    tx = int(random.uniform(-w*trans,w*trans))
    ty = int(random.uniform(-h*trans,h*trans))
    if z_enable:
        tz = int(random.uniform(-d*trans,d*trans))
    else:
        tz = 0
    dsx = slice(max(tx,0),min(tx+w,w))
    dsy = slice(max(ty,0),min(ty+h,h))
    dsz = slice(max(tz,0),min(tz+d,d))
    srx = slice(max(-tx,0),min(-tx+w,w))
    sry = slice(max(-ty,0),min(-ty+h,h))
    srz = slice(max(-tz,0),min(-tz+d,d))
    dst = np.zeros_like(src)
    mask_dst = np.zeros_like(mask)
    dst[dsy,dsx,dsz,:] = src[sry,srx,srz,:]
    mask_dst[dsy,dsx,dsz,:] = mask[sry,srx,srz,:]
    return dst,mask_dst
def crop_3d(src,mask,crop,z_enable=True):
    h,w,d = src.shape[:3]
    txm = int(random.uniform(0,w*crop))
    tym = int(random.uniform(0,h*crop))
    if z_enable:
        tzm = int(random.uniform(0,d*crop))
        tzmx = int(random.uniform(d*(1-crop),d-0.1))
    else:
        tzm = 0
        tzmx = d-1    
    txmx = int(random.uniform(w*(1-crop),w-0.1))
    tymx = int(random.uniform(h*(1-crop),h-0.1))
    dst = (src[tym:tymx+1,txm:txmx+1,tzm:tzmx+1,:]).copy()
    mask_dst = (mask[tym:tymx+1,txm:txmx+1,tzm:tzmx+1,:]).copy()
    return dst,mask_dst
def rotate(src,ang,scale,interp = cv2.INTER_LINEAR):
    dims = np.where(np.array(src.shape)==1)
    src = src.squeeze()
    h,w = src.shape[:2]
    center =(w/2,h/2)
    mat = cv2.getRotationMatrix2D(center, ang, scale)
    #print(src.shape)
    dst = cv2.warpAffine(src,mat,(w,h),flags=interp)
    dst = np.expand_dims(dst,tuple(dims[0]))
    return dst
def elastic(src,dx,dy,interp=cv2.INTER_LINEAR):
  # elastic deformation is a aug tric also applied in u-net
  # it was described in Best Practices for Convolutional Neural Networks applied to Visual Document Analysis 
  # as random distoration for every pixel
    dims = np.where(np.array(src.shape)==1)
    src = src.squeeze()
    nx, ny = dx.shape

    grid_y, grid_x = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")

    map_x = (grid_x + dx).astype(np.float32)
    map_y = (grid_y + dy).astype(np.float32)
    dst = cv2.remap(src, map_x, map_y, interpolation=interp, borderMode=cv2.BORDER_REFLECT)
    dst = np.expand_dims(dst,tuple(dims[0]))
    return dst
def flip(src,mask,dim):
    dst = np.flip(src,dim)
    mask =np.flip(mask,dim)
    return dst,mask
class Mydataset(data.Dataset):
    def __init__(self,cfg,mode='train',aug=True,test_mode='test'):
        self.cfg = cfg
        data = h5py.File(cfg.file_path,'r')
        self.mode = mode
        if mode!='test':
            self.masks = data[f'masks_{mode}']
            self.imgs = data[f'imgs_{mode}']
        else:
            self.offsets = data[f'offset_{test_mode}']
            self.imgs = data[f'imgs_{test_mode}']
            self.img_names = data[f'names_{test_mode}']
        self.size = cfg.tsize
        self.aug = aug
        self.cls_num = cfg.cls_num
        self.indices = cfg.indices
    def random_aug_3d(self,img,mask):
        if len(img.shape) < 4:
            img = np.expand_dims(img,axis=-1)
        if len(mask.shape) < 4:
            mask = np.expand_dims(mask,axis=-1)
        augs = random.sample(aug_options,k=random.randint(0,self.cfg.aug_num))
        if ('flip' in augs) and self.cfg.flip:
            if self.cfg.z_enable:
                dims = random.sample(range(3),k=random.randint(1,3))
            else:
                dims = random.sample(range(3),k=random.randint(1,2))
            for d in dims:  
                img,mask = flip(img,mask,dim=d)
        if ('trans' in augs) and self.cfg.trans:
            img,mask = translate_3d(img,mask,self.cfg.trans,self.cfg.z_enable)
        if ('crop' in augs) and self.cfg.crop:
            img,mask = crop_3d(img,mask,self.cfg.crop,self.cfg.z_enable)
        if ('rot' in augs) and self.cfg.rot:
            h,w,d = img.shape[:3]
            if self.cfg.z_enable:
                dims = random.sample(range(3),k=random.randint(1,3))
            else:
                dims = random.sample(range(2),k=random.randint(1,2))
            for idx in dims:
               slices=[slice(0,h),slice(0,w),slice(0,d)]
               ang = random.uniform(-self.cfg.rot,self.cfg.rot)
               scale = random.uniform(1-self.cfg.scale,1+self.cfg.scale)
               for i in range(img.shape[idx]):
                   slices[idx] = slice(i,i+1)
                   img[slices[0],slices[1],slices[2],:]= rotate(img[slices[0],slices[1],slices[2],:],ang,scale)
                   mask[slices[0],slices[1],slices[2],:] = rotate(mask[slices[0],slices[1],slices[2],:],ang,scale,interp=cv2.INTER_NEAREST)
        if ('elastic' in augs) and self.cfg.elastic:
            h,w,d = img.shape[:3]
            mu = 0
            sigma = random.uniform(0,self.cfg.elastic)

            dx = np.random.normal(mu, sigma, (3,3))
            dx_img = cv2.resize(dx, (w,h), interpolation=cv2.INTER_CUBIC)

            dy = np.random.normal(mu, sigma, (3,3))
            dy_img = cv2.resize(dy, (w,h), interpolation=cv2.INTER_CUBIC)

            for z in range(d):
                img[:, :, z, :] = elastic(img[:,:,z,:],dx_img, dy_img,cv2.INTER_CUBIC)
                mask[:, :, z, :] = elastic(mask[:, :, z, :], dx_img, dy_img, cv2.INTER_NEAREST)
            if self.cfg.z_enable:
                dx_img = np.zeros((w,d))
                dy = np.random.normal(mu, sigma, (3,3))
                dy_img = cv2.resize(dx, (d,w), interpolation=cv2.INTER_CUBIC)
                for y in range(h):
                    img[y, ...] = elastic(img[y,...],dx_img, dy_img)
                    mask[y, ...] = elastic(mask[y,...], dx_img, dy_img, cv2.INTER_NEAREST)
        if ('intensity' in augs) and self.cfg.intensity:
            for i in range(img.shape[-1]): 
                img[:, :, :, i] *= (1 + np.random.uniform(-self.cfg.intensity,self.cfg.intensity))
        if img.shape[:3] != self.size:
            img = crop_or_pad_slice_to_size(img,self.size,1)
            mask = crop_or_pad_slice_to_size(mask,self.size,1)
        return img,mask,augs
    def __len__(self):
        return len(self.imgs)
    def img_to_tensor(self,img):
        if len(img.shape) < 4:
            img = np.expand_dims(img,axis=-1)
        img = np.transpose(img,[3,0,1,2]).copy()
        data = torch.tensor(img,dtype=torch.float)
        return data
    def gen_gts(self,mask):
        #transfer to one-hot
        mask = mask.squeeze()
        h,w,d = mask.shape[:3]
        labels = torch.zeros((self.cls_num+1,h,w,d),dtype=torch.float)
        for i,idx in enumerate(self.indices):
            labels[i,...] = torch.tensor(mask == idx)
        return labels
    def __getitem__(self,idx):
        #print(name)
        img = self.imgs[idx]
        if self.mode!='test':            
            mask = self.masks[idx]
            if (self.mode=='train') and self.aug:
                img,mask,_ = self.random_aug_3d(img,mask)
            labels = self.gen_gts(mask)
            data = self.img_to_tensor(img)
            return data,labels      
        else:
            offset = torch.tensor(self.offsets[idx])
            imgName = self.img_names[idx]
            return data,offset,imgName

                





