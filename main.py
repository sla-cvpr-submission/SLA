import configargparse, os

from ast import literal_eval
import torch
from torch.utils.tensorboard import SummaryWriter

import sys
sys.path.append('src')

from model import ResModel, ProtoClassifier
from util import set_seed, save, load, LR_Scheduler
from dataset import get_all_loaders
from evaluation import evaluation
from mdh import ModelHandler

def arguments_parsing():
    p = configargparse.ArgumentParser(config_file_parser_class=configargparse.YAMLConfigFileParser)
    p.add('--config', is_config_file=True, default='./config.yaml')
    p.add('--device', type=str, default='0')
    p.add('--method', type=str, default='base')

    p.add('--dataset', type=str, default='OfficeHome')
    p.add('--source', type=int, default=0)
    p.add('--target', type=int, default=1)

    # training settings
    p.add('--seed', type=int, default=2020)
    p.add('--bsize', type=int, default=24)
    p.add('--num_iters', type=int, default=5000)
    p.add('--shot', type=str, default='3shot', choices=['1shot', '3shot'])
    p.add('--alpha', type=float, default=0.3)

    p.add('--eval_interval', type=int, default=500)
    p.add('--log_interval', type=int, default=100)
    p.add('--update_interval', type=int, default=0)
    p.add('--early', type=int, default=5000)
    p.add('--warmup', type=int, default=0)
    # configurations
    p.add('--dataset_cfg', type=literal_eval)
    
    # optimizer
    p.add('--lr', type=float, default=0.01)
    p.add('--momentum', type=float, default=0.9)
    p.add('--weight_decay', type=float, default=5e-4)
    p.add('--T', type=float, default=0.6)

    p.add('--note', type=str, default='')
    p.add('--order', type=int, default=0)
    p.add('--init', type=str, default='')
    return p.parse_args()

def main(args):
    os.environ['CUDA_VISIBLE_DEVICES'] = args.device
    set_seed(args.seed)

    model = ResModel('resnet34', output_dim=args.dataset['num_classes']).cuda()
    params = model.get_params(args.lr)
    opt = torch.optim.SGD(params, momentum=args.momentum, weight_decay=args.weight_decay, nesterov=True)
    lr_scheduler = LR_Scheduler(opt, args.num_iters)

    s_train_loader, s_test_loader, t_labeled_train_loader, t_labeled_test_loader, t_unlabeled_train_loader, t_unlabeled_test_loader, t_val_loader = get_all_loaders(args)

    if 'LC' in args.method:
        ppc = ProtoClassifier(args.dataset['num_classes'])

    torch.cuda.empty_cache()

    s_iter = iter(s_train_loader)
    u_iter = iter(t_unlabeled_train_loader)
    l_iter = iter(t_labeled_train_loader)

    model.train()

    writer = SummaryWriter(args.mdh.getLogPath())
    writer.add_text('Hash', args.mdh.getHashStr())

    counter = 0
    best_acc, best_val_acc = 0, 0

    for i in range(1, args.num_iters+1):
        opt.zero_grad()

        sx, sy, _ = next(s_iter)
        sx, sy = sx.float().cuda(), sy.long().cuda()

        if 'CDAC' in args.method:
            ux, _, ux1, ux2, u_idx = next(u_iter)
            ux, ux1, ux2, u_idx = ux.float().cuda(), ux1.float().cuda(), ux2.float().cuda(), u_idx.long()
        else:  
            ux, _, u_idx = next(u_iter)
            ux, u_idx = ux.float().cuda(), u_idx.long()

        sf = model.get_features(sx)

        if args.warmup > 0 and i > args.warmup and 'LC' in args.method:
            sy2 = ppc(sf.detach(), args.T)
            s_loss = model.lc_loss(sf, sy, sy2, args.alpha)
        elif 'NL' in args.method:
            s_loss = model.nl_loss(sf, sy, args.alpha, args.T)
        else:
            s_loss = model.feature_base_loss(sf, sy)
        
        tx, ty, _ = next(l_iter)
        tx, ty = tx.float().cuda(), ty.long().cuda()
        t_loss = model.base_loss(tx, ty)
        loss = (s_loss + t_loss) / 2
        loss.backward()
        opt.step()

        # update unlabeled target loss
        opt.zero_grad()
        if 'MME' in args.method:  
            u_loss = model.mme_loss(ux)
            u_loss.backward()
        elif 'CDAC' in args.method:
            u_loss = model.cdac_loss(ux, ux1, ux2, i)
            u_loss.backward()
        opt.step()

        # update PPC
        if 'LC' in args.method and args.warmup > 0 and i == args.warmup:
            ppc.init(model, t_unlabeled_test_loader)
            lr_scheduler.refresh()

        if i > args.warmup and args.update_interval > 0 and i % args.update_interval == 0 and 'LC' in args.method:
            ppc.init(model, t_unlabeled_test_loader)

        lr_scheduler.step()

        # logging
        if i % args.log_interval == 0:
            writer.add_scalar('LR', lr_scheduler.get_lr(), i)
            writer.add_scalar('Loss/s_loss', s_loss.item(), i)
            writer.add_scalar('Loss/t_loss', t_loss.item(), i)

            if 'MME' in args.method or 'CDAC' in args.method:
                writer.add_scalar('Loss/u_loss', -u_loss.item(), i)

        # early-stopping
        if i >= args.early and i % args.eval_interval == 0:
            val_acc = evaluation(t_val_loader, model)
            writer.add_scalar('Acc/val_acc', val_acc, i)
            if val_acc >= best_val_acc:
                best_val_acc = val_acc
                counter = 0

                t_acc = evaluation(t_unlabeled_test_loader, model)
                writer.add_scalar('Acc/t_acc', t_acc, i)
                save(args.mdh.getModelPath(), model)
                best_acc = t_acc
            else:
                counter += 1
            if counter > 5 or i == args.num_iters:
                writer.add_scalar('Acc/final_acc', best_acc, 0)
                break
    
if __name__ == '__main__':
    args = arguments_parsing()
    args.mdh = ModelHandler(args, keys=['dataset', 'method', 'source', 'target', 'seed', 'num_iters', 'alpha', 'T', 'init', 'note', 'update_interval', 'lr', 'order', 'shot', 'warmup'])
    # replace the configuration
    args.dataset = args.dataset_cfg[args.dataset]
    main(args)
