import numpy as np
import torch
import os
from collections import OrderedDict
from torch.autograd import Variable
import util.util as util
from util.image_pool import ImagePool
from .base_model import BaseModel
from . import networks
import torch.nn as nn
import torch.nn.functional as F
import itertools
import torchvision

### Feature Transformer Network
### http://pytorch.org/tutorials/intermediate/spatial_transformer_tutorial.html
def rotation_tensor(yaw, n_comps, gpu):
    yaw = yaw.unsqueeze(1)
    one = Variable(torch.ones(n_comps, 1, 1).cuda(gpu, async=True))
    zero = Variable(torch.zeros(n_comps, 1, 1).cuda(gpu, async=True))

    # print yaw, one, zero
    rot_z = torch.cat((
        torch.cat((yaw.cos(), -yaw.sin(), zero), 1),
        torch.cat((yaw.sin(), yaw.cos(), zero), 1),
        torch.cat((zero, zero, one), 1)
    ), 2)
    return rot_z

class FTAE_pyramid(nn.Module):
    def __init__(self, input_nc, ndf=64, n_layers=7, n_bilinear_layers=0,
                 norm_layer=None, nl_layer_enc=None, nl_layer_dec=None, gpu_ids=[],nz=200, use_vae=False):
        super(FTAE_pyramid, self).__init__()
        self.gpu_ids = gpu_ids
        self.use_vae = use_vae

        kw, padw = 4, 1

        enc = [nn.Conv2d(input_nc, ndf, kernel_size=kw, stride=2, padding=padw), nl_layer_enc()]

        nf_mult = 1
        for n in range(1, n_layers):
            nf_mult_prev = nf_mult
            nf_mult = min(2**n, 4)
            enc += [
                nn.Conv2d(ndf * nf_mult_prev, ndf * nf_mult,
                          kernel_size=kw, stride=2, padding=padw)]
            if norm_layer is not None and n < n_layers-1:
                enc += [norm_layer(ndf * nf_mult)]
                enc += [nl_layer_enc()]
        # sequence += [nn.AvgPool2d(8)]
        self.enc = nn.Sequential(*enc)
        self.fc = nn.Sequential(*[nn.Linear(ndf * nf_mult, nz*3), nn.LeakyReLU(0.2, True)])
        if use_vae:
            self.fc_var = nn.Sequential(*[nn.Linear(ndf * nf_mult, nz * 3), nn.LeakyReLU(0.2, True)])
        self.fc2 = nn.Sequential(*[nn.Linear(nz*3, ndf * nf_mult), nn.LeakyReLU(0.2, True)])

        deconv_layers = []

        for n in range(1, n_layers):
            nf_mult_prev = nf_mult
            nf_mult = min(2**(n_layers - n - 1), 4)
            upsample = 'bilinear' if n_layers - n < n_bilinear_layers else 'basic'
            deconv = networks.upsampleLayer(ndf * nf_mult_prev, ndf * nf_mult, upsample=upsample)
            if norm_layer is not None and (n_layers - n + 1) < n_layers:
                deconv += [norm_layer(ndf * nf_mult)]
            deconv += [nl_layer_dec()]
            deconv_layers.append( nn.Sequential(*deconv).cuda() )
            self.add_module("deconv_%d"%n, deconv_layers[-1])

        if n_bilinear_layers > 0:
            deconv = networks.upsampleLayer(ndf, 2, upsample='bilinear')
        else:
            deconv = networks.upsampleLayer(ndf, 2, upsample='basic')
        deconv_layers.append(nn.Sequential(*deconv).cuda())
        self.add_module("deconv_%d" % n_layers, deconv_layers[-1])

        self.deconv_layers = deconv_layers
        self.nz = nz


    def forward(self, x, yaw, Tz=0 ):
        z_conv = self.enc(x)
        if self.use_vae:
            mu = self.fc(z_conv.view(x.size(0),-1) )
            logvar = self.fc_var(z_conv.view(x.size(0),-1) )
            std = logvar.mul(0.5).exp_()
            eps = get_z_random(std.size(0), std.size(1), 'gauss')
            z_fc = eps.mul(std).add_(mu).view(x.size(0), self.nz, 3)
            self.mu = mu
            self.logvar = logvar
        else:
            z_fc = self.fc(z_conv.view(x.size(0),-1) ).view(x.size(0), self.nz, 3)
        # z_fc = F.tanh(z_fc)

        R = rotation_tensor(yaw, x.size(0), self.gpu_ids[0])
        z_rot = z_fc.bmm(R) # + T
        z_rot_fc = self.fc2(z_rot.view(x.size(0), self.nz*3))

        flows = []
        output = self.deconv_layers[0](z_rot_fc.view(z_conv.size(0),z_conv.size(1),z_conv.size(2),z_conv.size(3)))
        for layers in self.deconv_layers[1:] :

            output = layers(output)
            flows.append( F.tanh(output[:,:2,:,:]) )
            # print output.size()

        return flows


    def get_mu_var(self):
        return self.mu,self.logvar

class FTAE(nn.Module):
    def __init__(self, input_nc, ndf=64, n_layers=7, n_bilinear_layers=0,
                 norm_layer=None, nl_layer_enc=None, nl_layer_dec=None, gpu_ids=[], nz=200, use_vae=False):
        super(FTAE, self).__init__()
        self.gpu_ids = gpu_ids
        self.use_vae = use_vae

        kw, padw = 4, 1

        enc = [nn.Conv2d(input_nc, ndf, kernel_size=kw, stride=2, padding=padw), nl_layer_enc()]

        nf_mult = 1
        for n in range(1, n_layers):
            nf_mult_prev = nf_mult
            nf_mult = min(2 ** n, 4)
            enc += [
                nn.Conv2d(ndf * nf_mult_prev, ndf * nf_mult,
                          kernel_size=kw, stride=2, padding=padw)]
            if norm_layer is not None and n < n_layers - 1:
                enc += [norm_layer(ndf * nf_mult)]
                enc += [nl_layer_enc()]
        # sequence += [nn.AvgPool2d(8)]
        self.enc = nn.Sequential(*enc)
        self.fc = nn.Sequential(*[nn.Linear(ndf * nf_mult, nz * 3), nn.LeakyReLU(0.2, True)])
        if use_vae:
            self.fc_var = nn.Sequential(*[nn.Linear(ndf * nf_mult, nz * 3), nn.LeakyReLU(0.2, True)])
        self.fc2 = nn.Sequential(*[nn.Linear(nz * 3, ndf * nf_mult), nn.LeakyReLU(0.2, True)])

        deconv = []
        for n in range(1, n_layers):
            nf_mult_prev = nf_mult
            nf_mult = min(2 ** (n_layers - n - 1), 4)

            upsample = 'bilinear' if n_layers - n < n_bilinear_layers else 'basic'
            deconv += networks.upsampleLayer(ndf * nf_mult_prev, ndf * nf_mult, upsample=upsample)
            if norm_layer is not None and (n_layers - n + 1) < n_layers:
                deconv += [norm_layer(ndf * nf_mult)]
            deconv += [nl_layer_dec()]

        if n_bilinear_layers > 0:
            deconv += networks.upsampleLayer(ndf, 2, upsample='bilinear')
        else:
            deconv += networks.upsampleLayer(ndf, 2, upsample='basic')

        deconv += [nn.Tanh()]

        self.deconv = nn.Sequential(*deconv)
        self.nz = nz

    def forward(self, x, yaw, Tz=0 ):
        z_conv = self.enc(x)
        if self.use_vae:
            mu = self.fc(z_conv.view(x.size(0),-1) )
            logvar = self.fc_var(z_conv.view(x.size(0),-1) )
            std = logvar.mul(0.5).exp_()
            eps = get_z_random(std.size(0), std.size(1), 'gauss')
            z_fc = eps.mul(std).add_(mu).view(x.size(0), self.nz, 3)
            self.mu = mu
            self.logvar = logvar
        else:
            z_fc = self.fc(z_conv.view(x.size(0),-1) ).view(x.size(0), self.nz, 3)
        # z_fc = F.tanh(z_fc)

        R = rotation_tensor(yaw, x.size(0), self.gpu_ids[0])
        T = np.array([1, 0, 0])
        T = Variable(torch.from_numpy(T.astype(np.float32))).cuda().expand(x.size(0),self.nz,3)
        z_fc += T
        z_rot = z_fc.bmm(R) # + T
        z_rot_fc = self.fc2(z_rot.view(x.size(0), self.nz*3))



        output = self.deconv(z_rot_fc.view(z_conv.size(0),z_conv.size(1),z_conv.size(2),z_conv.size(3)))
        return output


    def get_mu_var(self):
        return self.mu,self.logvar


class MultiViewModel(BaseModel):
    def name(self):
        return 'MultiViewModel'

    def initialize(self, opt):
        BaseModel.initialize(self, opt)
        self.isTrain = opt.isTrain
        # self.yaw = Variable(torch.Tensor([np.pi/4.]).cuda(opt.gpu_ids[0], async=True), requires_grad=False)
        # load/define networks
        input_nc = opt.input_nc + 2 if opt.concat_grid else opt.input_nc
        if opt.use_pyramid:
            self.netG = FTAE_pyramid(input_nc, opt.ngf, n_layers=int(np.log2(opt.fineSize)), n_bilinear_layers=opt.n_bilinear_layers,
                         norm_layer=networks.get_norm_layer(norm_type=opt.norm),
                         nl_layer_enc=networks.get_non_linearity(layer_type=opt.nl_enc),
                         nl_layer_dec=networks.get_non_linearity(layer_type=opt.nl_dec),gpu_ids=opt.gpu_ids,
                         nz=opt.nz, use_vae=opt.use_vae)
        else:
            self.netG = FTAE(input_nc, opt.ngf, n_layers=int(np.log2(opt.fineSize)), n_bilinear_layers=opt.n_bilinear_layers,
                         norm_layer=networks.get_norm_layer(norm_type=opt.norm),
                         nl_layer_enc=networks.get_non_linearity(layer_type=opt.nl_enc),
                         nl_layer_dec=networks.get_non_linearity(layer_type=opt.nl_dec),gpu_ids=opt.gpu_ids,
                         nz=opt.nz, use_vae=opt.use_vae)

        if len(opt.gpu_ids) > 0:
            self.netG.cuda(opt.gpu_ids[0])
        networks.init_weights(self.netG, init_type="normal")

        if self.isTrain:
            use_sigmoid = opt.no_lsgan
            self.netD = networks.define_D(opt.input_nc + opt.output_nc, opt.ndf,
                                          opt.which_model_netD,
                                          opt.n_layers_D, opt.norm, use_sigmoid, opt.init_type, self.gpu_ids)
        if not self.isTrain or opt.continue_train:
            self.load_network(self.netG, 'G', opt.which_epoch)
            if self.isTrain:
                self.load_network(self.netD, 'D', opt.which_epoch)

        if self.isTrain:
            self.fake_AB_pool = ImagePool(opt.pool_size)
            self.old_lr = opt.lr
            # define loss functions
            self.criterionGAN = networks.GANLoss(use_lsgan=not opt.no_lsgan, tensor=self.Tensor)
            self.criterionL1 = torch.nn.L1Loss()
            self.criterionTV = networks.TVLoss()

            # initialize optimizers
            self.schedulers = []
            self.optimizers = []
            self.optimizer_G = torch.optim.Adam(itertools.chain(self.netG.parameters()),  #, [self.yaw]
                                                lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_D = torch.optim.Adam(self.netD.parameters(),
                                                lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D)
            for optimizer in self.optimizers:
                self.schedulers.append(networks.get_scheduler(optimizer, opt))

        grid = np.zeros((opt.fineSize,opt.fineSize,2))
        for i in range(grid.shape[0]):
            for j in range(grid.shape[1]):
                grid[i,j,0] = j
                grid[i,j,1] = i
        grid /= (opt.fineSize/2)
        grid -= 1
        self.grid = torch.from_numpy(grid).cuda().float() #Variable(torch.from_numpy(grid))
        self.grid = self.grid.view(1,self.grid.size(0),self.grid.size(1),self.grid.size(2)).expand(opt.batchSize,opt.fineSize,opt.fineSize,2)
        self.grid = Variable(self.grid)

        self.view0 = Variable(torch.Tensor([0]).cuda(self.gpu_ids[0], async=True).view(1,1).expand(opt.batchSize,1))
        self.view1 = Variable(torch.Tensor([np.pi / 4.]).cuda(self.gpu_ids[0], async=True).view(1,1).expand(opt.batchSize,1))

        print('---------- Networks initialized -------------')
        networks.print_network(self.netG)
        if self.isTrain:
            networks.print_network(self.netD)
        print('-----------------------------------------------')

    def set_input(self, input):
        AtoB = self.opt.which_direction == 'AtoB'
        input_A = input['A' if AtoB else 'B']
        input_B = input['B' if AtoB else 'A']
        input_Yaw = input['Yaw']

        if len(self.gpu_ids) > 0:
            input_A = input_A.cuda(self.gpu_ids[0], async=True)
            input_B = input_B.cuda(self.gpu_ids[0], async=True)
            input_Yaw = input_Yaw.cuda(self.gpu_ids[0], async=True)

        self.input_A = input_A
        self.input_B = input_B
        self.input_Yaw = input_Yaw
        # print input_Yaw.size()

        self.image_paths = input['A_paths' if AtoB else 'B_paths']

        if self.opt.dataset_mode == 'aligned_with_C':
            input_C = input['C']
            if len(self.gpu_ids) > 0:
                self.input_C = input_C.cuda(self.gpu_ids[0], async=True)

        #
        self.mask = torch.sum(self.input_B, dim=1)
        self.mask = (self.mask < 3.0).unsqueeze(1)
        self.mask = self.mask.expand(self.input_B.size(0),2,self.input_B.size(2),self.input_B.size(3))
        #
        self.mask0 = torch.sum(self.input_A, dim=1)
        self.mask0 = (self.mask0 < 3.0).unsqueeze(1)
        self.mask0 = self.mask0.expand(self.input_B.size(0),2,self.input_B.size(2),self.input_B.size(3))


    def forward(self):
        add_grid = self.opt.add_grid
        rectified = self.opt.rectified
        self.real_A = Variable(self.input_A)
        self.real_B = Variable(self.input_B)
        self.real_Yaw = Variable(self.input_Yaw)

        if self.opt.dataset_mode == 'aligned_with_C':
            self.real_C = Variable(self.input_C)+self.grid

        if self.opt.concat_grid:
            b = self.real_A.size(0)
            real_A_grid = torch.cat([self.real_A, self.grid[:b, :, :, :].permute(0,3,1,2)], dim=1)
        else:
            real_A_grid = self.real_A

        if not self.opt.use_pyramid:
            self.fake_B_flow = self.netG(real_A_grid, self.real_Yaw, self.grid)
            self.fake_B_flow_converted = convert_flow(self.fake_B_flow,self.grid,add_grid,rectified)
            self.fake_B = torch.nn.functional.grid_sample(self.real_A, self.fake_B_flow_converted)
        else:
            self.real_B_pyramid = []
            self.fake_B_pyramid = []
            flow_pyramid = self.netG(real_A_grid, self.real_Yaw, self.grid)
            real_A_downsampled = self.real_A
            real_B_downsampled = self.real_B

            for flow in reversed(flow_pyramid[-3:]):
                self.real_B_pyramid.append(real_B_downsampled)
                flow_converted = convert_flow(flow, self.grid, add_grid, rectified)
                self.fake_B_pyramid.append(torch.nn.functional.grid_sample(real_A_downsampled, flow_converted))
                real_A_downsampled = F.avg_pool2d(real_A_downsampled,2)
                real_B_downsampled = F.avg_pool2d(real_B_downsampled,2)

            self.fake_B_flow = flow_pyramid[-1]
            self.fake_B_flow_converted = convert_flow(self.fake_B_flow, self.grid, add_grid, rectified)
            self.fake_B = self.fake_B_pyramid[0]

        if self.opt.lambda_kl > 0:
            self.mu,self.logvar = self.netG.get_mu_var()

    # no backprop gradients
    def test(self):
        add_grid = self.opt.add_grid
        rectified = self.opt.rectified
        self.real_A = Variable(self.input_A, volatile=True)
        self.real_B = Variable(self.input_B, volatile=True)
        self.fake_B_list = []

        NV = 10
        for i in range(NV):
            if self.opt.concat_grid:
                b = self.real_A.size(0)
                real_A_grid = torch.cat([self.real_A, self.grid[:b, :, :, :].permute(0, 3, 1, 2)], dim=1)
            else:
                real_A_grid = self.real_A

            fake_B_flow = self.netG(real_A_grid, Variable(torch.Tensor([-1/4.*np.pi + 1/2.*np.pi*i/(NV-1) ]).cuda(self.gpu_ids[0], async=True)).unsqueeze(0))
            if self.opt.use_pyramid:
                fake_B_flow = fake_B_flow[-1]
            fake_B = torch.nn.functional.grid_sample(self.real_A, convert_flow(fake_B_flow,self.grid,add_grid,rectified))
            self.fake_B_list.append(fake_B)
        # np.save(os.path.join("./results/features", os.path.basename(self.image_paths[0]) ), z.data.cpu().numpy())

    # get image paths
    def get_image_paths(self):
        return self.image_paths

    def backward_G(self):
        # First, G(A) should fake the discriminator
        # fake_AB = torch.cat((self.real_A, self.fake_B), 1)
        # pred_fake = self.netD(fake_AB)
        # self.loss_G_GAN = self.opt.lambda_gan * self.criterionGAN(pred_fake, True)
        # Total variation loss

        self.loss_TV = self.criterionTV(self.fake_B_flow) * self.opt.lambda_tv

        if self.opt.lambda_flow > 0:
            self.loss_G_flow = self.criterionL1(self.fake_B_flow_converted.permute(0,3,1,2)[self.mask],
                                                self.real_C.permute(0,3,1,2)[self.mask]) * self.opt.lambda_flow
        else:
            self.loss_G_flow = 0. * self.loss_TV

        # KL loss
        if self.opt.lambda_kl > 0:
            kl_element = self.mu.pow(2).add_(self.logvar.exp()).mul_(-1).add_(1).add_(self.logvar)
            self.loss_kl = torch.sum(kl_element).mul_(-0.5) * self.opt.lambda_kl
        else:
            self.loss_kl = 0. * self.loss_TV

        # Second, G(A) = B
        if not self.opt.use_pyramid:
            self.loss_G_L1 = self.criterionL1(self.fake_B, self.real_B) * self.opt.lambda_A
        else:
            self.loss_G_L1 = 0
            for fake_B, real_B in zip(self.fake_B_pyramid, self.real_B_pyramid):
                self.loss_G_L1 += self.criterionL1(fake_B, real_B) * self.opt.lambda_A / len(self.real_B_pyramid)

        self.loss_G = self.loss_G_L1 \
                      + self.loss_TV + self.loss_G_flow + self.loss_kl

        self.loss_G.backward()

    def optimize_parameters(self):
        self.forward()

        # self.optimizer_D.zero_grad()
        # self.backward_D()
        # self.optimizer_D.step()

        self.optimizer_G.zero_grad()
        self.backward_G()
        self.optimizer_G.step()

    def get_current_errors(self):
        return OrderedDict([('G_L1', self.loss_G_L1.data[0]),
                            ('F_L1', self.loss_G_flow.data[0]),
                            ('KL', self.loss_kl.data[0]),
                            ('TV', self.loss_TV.data[0]),
                            ])

    def get_current_visuals(self):
        if not self.opt.isTrain:
            return self.get_current_visuals_test()
        real_A = util.tensor2im(self.real_A.data)
        fake_B = util.tensor2im(self.fake_B.data)
        real_B = util.tensor2im(self.real_B.data)
        flow = util.tensor2im(self.fake_B_flow_converted.permute(0,3,1,2).data)

        if self.opt.dataset_mode == 'aligned_with_C':
            real_flow = util.tensor2im(self.real_C.permute(0,3,1,2).data)
        else:
            real_flow = util.tensor2im(self.fake_B_flow_converted.permute(0, 3, 1, 2).data)
        return OrderedDict([('real_A', real_A), ('fake_B', fake_B), ('real_B', real_B),
                            ('flow',flow), ('real_flow', real_flow)])

    def get_current_visuals_test(self):
        real_A = util.tensor2im(self.real_A.data)
        real_B = util.tensor2im(self.real_B.data)
        visual_list = OrderedDict([('real_A', real_A)])
        for idx,fake_B_var in enumerate(self.fake_B_list):
            visual_list['%d'%idx] = util.tensor2im(fake_B_var.data)
        visual_list['real_B'] = real_B
        return visual_list

    def save(self, label):
        self.save_network(self.netG, 'G', label, self.gpu_ids)
        self.save_network(self.netD, 'D', label, self.gpu_ids)

def flip(x, dim):
    dim = x.dim() + dim if dim < 0 else dim
    return x[tuple(slice(None, None) if i != dim
             else torch.arange(x.size(i)-1, -1, -1).long()
             for i in range(x.dim()))]

def convert_flow(flow, grid, add_grid=False, rectified=False):
    b,_,h,w = flow.size()
    flow_ret = flow.permute(0, 2, 3, 1)
    if rectified:
        flow_ret = torch.cat([flow_ret[:,:,:,0].unsqueeze(3),grid[:b,:,:,1].unsqueeze(3),], dim=3)
        if add_grid:
            grid_new = torch.cat([grid[:b,:,:,0].unsqueeze(3),Variable(torch.zeros(b,h,w,1).cuda()),], dim=3)
            flow_ret += grid_new
    elif add_grid:
        flow_ret = flow_ret + grid[:b,:,:,:]
    return flow_ret

def get_z_random(batchSize, nz, random_type='gauss'):
    if random_type == 'uni':
        z = torch.rand(batchSize, nz) * 2.0 - 1.0
    elif random_type == 'gauss':
        z = torch.randn(batchSize, nz)
    z = Variable(z.cuda())
    return z

# R = np.array(
        #    [ [np.cos(yaw),-np.sin(yaw), 0],
        #     [np.sin(yaw), np.cos(yaw), 0],
        #     [        0,         0, 1]]
        # )
        # R = np.array(
        #    [[        1,         0, 0],
        #     [0,np.cos(yaw),-np.sin(yaw)],
        #     [0,np.sin(yaw), np.cos(yaw)]
        #     ]
        # )
        # R = np.array(
        #    [[np.cos(yaw),0,-np.sin(yaw)],
        #     [0, 1, 0],
        #     [np.sin(yaw),0, np.cos(yaw)]
        #     ]
        # )
        # R = np.eye(3,3)
        # T = np.array([Tz,0,0])
        # R = Variable(torch.from_numpy(R.astype(np.float32))).cuda().expand(x.size(0),3,3)
        # T = Variable(torch.from_numpy(T.astype(np.float32))).cuda().expand(x.size(0),self.nz,3)

# def __init__(self, input_nc, ndf=64, n_layers=7, upsample='basic',
#              norm_layer=None, nl_layer=None, gpu_ids=[], nz=200, use_vae=False):
#     super(FTAE, self).__init__()
#     self.gpu_ids = gpu_ids
#     self.use_vae = use_vae
#
#     kw, padw = 4, 1
#
#     enc = [nn.Conv2d(input_nc, ndf, kernel_size=kw, stride=2, padding=padw), nl_layer()]
#
#     nf_mult = 1
#     for n in range(1, n_layers):
#         nf_mult_prev = nf_mult
#         nf_mult = min(2 ** n, 4)
#         enc += [
#             nn.Conv2d(ndf * nf_mult_prev, ndf * nf_mult,
#                       kernel_size=kw, stride=2, padding=padw)]
#         if norm_layer is not None and n < n_layers - 1:
#             enc += [norm_layer(ndf * nf_mult)]
#             enc += [nl_layer()]
#     # sequence += [nn.AvgPool2d(8)]
#     self.enc = nn.Sequential(*enc)
#     self.fc = nn.Sequential(*[nn.Linear(ndf * nf_mult, nz * 3), nn.LeakyReLU(0.2, True)])
#     if use_vae:
#         self.fc_var = nn.Sequential(*[nn.Linear(ndf * nf_mult, nz * 3), nn.LeakyReLU(0.2, True)])
#     self.fc2 = nn.Sequential(*[nn.Linear(nz * 3, ndf * nf_mult), nn.LeakyReLU(0.2, True)])
#
#     deconv = []
#     for n in range(1, n_layers):
#         nf_mult_prev = nf_mult
#         nf_mult = min(2 ** (n_layers - n - 1), 4)
#         deconv += networks.upsampleLayer(ndf * nf_mult_prev, ndf * nf_mult, upsample=upsample)
#         if norm_layer is not None and (n_layers - n + 1) < n_layers:
#             deconv += [norm_layer(ndf * nf_mult)]
#         deconv += [nl_layer()]
#     deconv += networks.upsampleLayer(ndf, 2, upsample='bilinear')
#     deconv += [nn.Tanh()]
#
#     self.deconv = nn.Sequential(*deconv)
#     self.nz = nz
