import time
import os
from options.test_options import TestOptions
from data.data_loader import CreateDataLoader
from models.models import create_model
from util.visualizer import Visualizer
from util import html

opt = TestOptions().parse()
opt.nThreads = 1   # test code only supports nThreads = 1
opt.batchSize = 1  # test code only supports batchSize = 1
opt.serial_batches = True  # no shuffle
opt.no_flip = True  # no flip

model = create_model(opt)
visualizer = Visualizer(opt)


for v in range(18):
    opt.idx_source_view = v
    # create website
    data_loader = CreateDataLoader(opt)
    dataset = data_loader.load_data()

    web_dir = os.path.join(opt.results_dir, opt.name, 'evaluate', '%d' % (v))
    webpage = html.HTML(web_dir, 'Experiment = %s, Phase = %s, Epoch = %s' % (opt.name, opt.phase, opt.which_epoch))

    for i, data in enumerate(dataset):
        if i >= opt.how_many:
            break
        model.set_input(data)
        model.test()
        visuals = model.get_current_visuals()
        img_path = model.get_image_paths()
        print('%02d.%04d: process image... %s' % (v, i, img_path))
        visualizer.save_images(webpage, visuals, img_path, aspect_ratio=opt.aspect_ratio)

webpage.save()
