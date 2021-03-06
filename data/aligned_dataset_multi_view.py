import os.path
import random
import torchvision.transforms as transforms
import torch
from data.base_dataset import BaseDataset, get_transform
from data.image_folder import make_dataset,make_dataset_label
from PIL import Image
import numpy as np

class AlignedDatasetMultiView(BaseDataset):
    def initialize(self, opt):
        self.opt = opt
        self.root = opt.dataroot
        self.dirs = []
        self.paths = []
        self.random_AB = opt.random_AB
        self.nv = 18
        self.train_split = opt.train_split

        for i in range(self.nv):
            self.dirs.append(os.path.join(opt.dataroot, "%d" %i) )
            self.paths.append(sorted(make_dataset(self.dirs[i]) ) )

        # if opt.phase == 'test':
        #     self.dirs[self.center_view] = os.path.join(opt.dataroot, "test")
        #     self.paths.append(sorted(make_dataset(self.dirs[self.center_view])))

        # self.dir_C  = os.path.join(opt.dataroot, opt.phase+"C")
        # self.C_paths = sorted(make_dataset_label(self.dir_C))

        self.transform = get_transform(opt)

    def __getitem__(self, index):


        if self.opt.phase == 'test':
            index += int(len(self.paths[int(self.nv/2)])*self.train_split)+1

        if self.random_AB:
            idx_A = np.random.randint(1, self.nv - 2)
            idx_B = idx_A + np.random.choice([-1,1])
        else:
            idx_A = int(self.nv/2)

            if self.opt.phase == 'train':
                if self.opt.ignore_center:
                    idx_B = np.random.randint(0, self.nv - 1)
                    if idx_B == idx_A:
                        idx_B = self.nv - 1
                else:
                    idx_B = np.random.randint(0, self.nv)
            else:
                idx_B = np.random.randint(0, self.nv)



        # if self.opt.phase == 'train':
        #     idx_A = np.random.choice((1,2,3))
        #     idx_B = 0 #np.random.choice((0,2))

        A = Image.open(self.paths[idx_A][index]).convert('RGB')
        A = self.transform(A)
        B = Image.open(self.paths[idx_B][index]).convert('RGB')
        B = self.transform(B)

        # print idx_B, index
        # C_path = self.C_paths[index]
        # C_arr = np.load(C_path)
        # if C_arr.shape[1] > self.opt.fineSize:
        #     stride = C_arr.shape[1]/self.opt.fineSize
        #     C_arr = C_arr[::int(stride),::int(stride),:]
        # else:
        #     C_arr = C_arr
        # C = torch.from_numpy(C_arr).float()


        if self.opt.which_direction == 'BtoA':
            input_nc = self.opt.output_nc
            output_nc = self.opt.input_nc
        else:
            input_nc = self.opt.input_nc
            output_nc = self.opt.output_nc

        if input_nc == 1:  # RGB to gray
            tmp = A[0, ...] * 0.299 + A[1, ...] * 0.587 + A[2, ...] * 0.114
            A = tmp.unsqueeze(0)

        if output_nc == 1:  # RGB to gray
            tmp = B[0, ...] * 0.299 + B[1, ...] * 0.587 + B[2, ...] * 0.114
            B = tmp.unsqueeze(0)

        yaw = (idx_A-idx_B) * np.pi/9.

        return {'A': A, 'B': B, 'Yaw': torch.Tensor([yaw]),
                'A_paths': self.paths[int(self.nv/2)][index], }

    def __len__(self):
        if self.opt.phase == 'train':
            return int(len(self.paths[int(self.nv/2)])*self.train_split)
        else:
            return int(len(self.paths[int(self.nv/2)])*(1-self.train_split) )

    def name(self):
        return 'AlignedDatasetMultiView'
