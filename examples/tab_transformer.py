import argparse
import os.path as osp
import sys
sys.path.append('../')

import torch
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score
from torch.utils.data import DataLoader
from torch.optim.lr_scheduler import ExponentialLR
from tqdm import tqdm

from rllm.datasets.titanic import Titanic
from rllm.nn.models.tab_transformer import TabTransformer

parser = argparse.ArgumentParser()
parser.add_argument('--dataset', type=str, default='titanic',
                    choices=["titanic",])
parser.add_argument('--dim', help='embedding dim', type=int, default=32)
parser.add_argument('--num_layers', type=int, default=6)
parser.add_argument('--num_heads', type=int, default=8)
parser.add_argument('--attn_dropout', type=float, default=0.3)
parser.add_argument('--ff_dropout', type=float, default=0.3)
parser.add_argument('--batch_size', type=int, default=128)
parser.add_argument('--lr', type=float, default=0.0001)
parser.add_argument('--epochs', type=int, default=50)
parser.add_argument('--seed', type=int, default=42)
parser.add_argument('--compile', action='store_true')
args = parser.parse_args()

torch.manual_seed(args.seed)
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# Prepare datasets
path = osp.join(osp.dirname(osp.realpath(__file__)), '..', 'data')
dataset = Titanic(cached_dir=path)[0]
dataset.to(device)
dataset.shuffle()

# Split dataset, here the ratio of train-val-test is 80%-10%-10%
train_dataset, val_dataset, test_dataset = dataset.get_dataset(0.2, 0.4, 0.4)
train_loader = DataLoader(train_dataset, batch_size=args.batch_size,
                          shuffle=True)
val_loader = DataLoader(val_dataset, batch_size=args.batch_size)
test_loader = DataLoader(test_dataset, batch_size=args.batch_size)

# Set up model and optimizer
model = TabTransformer(
    hidden_dim=args.dim,             # embedding dim, paper set at 32
    output_dim=dataset.num_classes,  # binary prediction, but could be anything
    layers=args.num_layers,          # depth, paper recommended 6
    heads=args.num_heads,            # heads, paper recommends 8
    attn_dropout=args.attn_dropout,  # post-attention dropout
    ff_dropout=args.ff_dropout,      # feed forward dropout
    mlp_hidden_mults=(4, 2),         # multiples of hidden dim of last mlp
    mlp_act=torch.nn.ReLU(),         # activation for final mlp
    col_stats_dict=dataset.stats_dict
).to(device)

model = torch.compile(model, dynamic=True) if args.compile else model
optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
lr_scheduler = ExponentialLR(optimizer, gamma=0.95)


def train(epoch: int) -> float:
    model.train()
    loss_accum = total_count = 0
    for batch in tqdm(train_loader, desc=f'Epoch: {epoch}'):
        feat_dict, y = batch
        pred = model.forward(feat_dict)
        loss = F.cross_entropy(pred, y.long())
        optimizer.zero_grad()
        loss.backward()
        loss_accum += float(loss) * y.size(0)
        total_count += y.size(0)
        optimizer.step()
    return loss_accum / total_count


@torch.no_grad()
def test(loader: DataLoader) -> float:
    model.eval()
    all_preds = []
    all_labels = []
    for batch in loader:
        feat_dict, y = batch
        pred = model.forward(feat_dict)

        all_labels.append(y.cpu())
        all_preds.append(pred[:, 1].detach().cpu())
    all_labels = torch.cat(all_labels).numpy()
    all_preds = torch.cat(all_preds).numpy()

    # Compute the overall AUC
    overall_auc = roc_auc_score(all_labels, all_preds)
    return overall_auc


metric = 'AUC'
best_val_metric = 0
best_test_metric = 0
for epoch in range(1, args.epochs + 1):
    train_loss = train(epoch)
    train_metric = test(train_loader)
    val_metric = test(val_loader)
    test_metric = test(test_loader)

    if val_metric > best_val_metric:
        best_val_metric = val_metric
        best_test_metric = test_metric

    print(f'Train Loss: {train_loss:.4f}, Train {metric}: {train_metric:.4f}, '
          f'Val {metric}: {val_metric:.4f}, Test {metric}: {test_metric:.4f}')
    lr_scheduler.step()

print(f'Best Val {metric}: {best_val_metric:.4f}, '
      f'Best Test {metric}: {best_test_metric:.4f}')
