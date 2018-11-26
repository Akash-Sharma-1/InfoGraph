import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from core.encoders import *
from data_utils import get_data
#from evaluate_embedding import evaluate_all
import json
from torch import optim

class GraphSkipgram(nn.Module):
  def __init__(self, args, dataset_sampler):
    super(GraphSkipgram, self).__init__()

    self.dataset_sampler = dataset_sampler

    self.embedding_dim = args.output_dim
    self.neg_sampling_size = args.neg_sampling_num
    max_num_nodes = dataset_sampler.max_num_nodes
    input_dim=args.input_dim
    hidden_dim = args.hidden_dim
    num_layers = args.num_gc_layers
    encoder_embedding_dim = self.embedding_dim + (num_layers-1)*hidden_dim

    initrange = -1.5 / self.embedding_dim

    if args.method == 'base':
        self.u_encoder = GcnEncoderGraph(input_dim=input_dim, 
                                            hidden_dim=hidden_dim,
                                            num_layers=num_layers,
                                            embedding_dim=self.embedding_dim,
                                            label_dim=None)

        self.v_encoder = GcnEncoderGraph(input_dim=input_dim, 
                                            hidden_dim=hidden_dim,
                                            num_layers=num_layers,
                                            embedding_dim=self.embedding_dim,
                                            label_dim=None)
    if args.method == 'base-set2set':
        self.u_encoder = GcnSet2SetEncoder(input_dim=input_dim, 
                                            hidden_dim=hidden_dim,
                                            num_layers=num_layers,
                                            embedding_dim=self.embedding_dim,
                                            label_dim=None)

        self.v_encoder = GcnSet2SetEncoder(input_dim=input_dim, 
                                            hidden_dim=hidden_dim,
                                            num_layers=num_layers,
                                            embedding_dim=self.embedding_dim,
                                            label_dim=None)
    if args.method == 'soft-assign':
        self.u_encoder = SoftPoolingGcnEncoder(max_num_nodes=max_num_nodes,
                                               input_dim=input_dim, 
                                               assign_hidden_dim=hidden_dim,
                                               hidden_dim=hidden_dim,
                                               num_layers=num_layers,
                                               embedding_dim=self.embedding_dim,
                                               label_dim=None)

        self.v_encoder = SoftPoolingGcnEncoder(max_num_nodes=max_num_nodes,
                                               input_dim=input_dim, 
                                               assign_hidden_dim=hidden_dim,
                                               hidden_dim=hidden_dim,
                                               num_layers=num_layers,
                                               embedding_dim=self.embedding_dim,
                                               label_dim=None)


    # self.u_embeddings = nn.Linear(250, embedding_dim) 
    # self.u_embeddings = nn.Linear(encoder_embedding_dim + input_dim, embedding_dim) 
    self.u_embeddings = nn.Linear(encoder_embedding_dim, self.embedding_dim) 


    # self.v_embeddings  = nn.Linear(250, embedding_dim) 
    # self.v_embeddings  = nn.Linear(self.encoder_embedding_dim + input_dim, embedding_dim) 
    self.v_embeddings  = nn.Linear(encoder_embedding_dim, self.embedding_dim) 
    self.init_emb()
    # with open('../esc/gitgraph-stars-names.json', 'r') as f:

        # self.names = json.load(f)

  def init_emb(self):
    initrange = -1.5 / self.embedding_dim
    self.u_embeddings.weight.data.uniform_(-initrange, initrange)
    self.v_embeddings.weight.data.uniform_(-0, 0)

  def enc(self, data, u=True):

    adj = Variable(data['adj'].float(), requires_grad=False).cuda()
    h0 = Variable(data['feats'].float()).cuda()
    #labels.append(data['label'].long().numpy())
    batch_num_nodes = data['num_nodes'].int().numpy()
    assign_input = Variable(data['assign_feats'].float(), requires_grad=False).cuda() 

    # node_cnt_features = torch.from_numpy(np.array([self.node_cnt_features[idx.item()] for idx in data['idxs']])).float().cuda()

    if u:
        ret = self.u_encoder(h0, adj, batch_num_nodes, assign_x=assign_input)
        # return torch.cat([ret, whole_graph_features], dim=1)
        return torch.cat([ret], dim=1)
    else:
        ret = self.v_encoder(h0, adj, batch_num_nodes, assign_x=assign_input)
        # return torch.cat([ret, whole_graph_features], dim=1)
        return torch.cat([ret], dim=1)

  def forward(self, u_pos, v_pos, v_neg, batch_size):

    neg_sampling_size = self.neg_sampling_size

    embed_u = self.u_embeddings(self.enc(u_pos, u=True))
    embed_v = self.v_embeddings(self.enc(v_pos, u=False))

    # embed_u = self.enc(u_pos, u=True)
    # embed_v = self.enc(v_pos, u=False)

    # print(embed_u.detach().cpu().numpy())
    # print(embed_v.detach().cpu().numpy())

    score  = torch.mul(embed_u, embed_v)
    score = torch.sum(score, dim=1)
    # print(score.detach().cpu().numpy())
    log_target = F.logsigmoid(score).squeeze()
    
    neg_embed_v = self.v_embeddings(self.enc(v_neg, u=False))
    neg_embed_v = neg_embed_v.view(batch_size, neg_sampling_size, self.embedding_dim)

    # neg_embed_v = self.enc(v_neg, u=False)
    # neg_embed_v = neg_embed_v.view(batch_size, neg_sampling_size, -1)

    # neg_embed_v: [batch_size, neg_sampling_size, embedding_size]
    # embed_u.unsqueeze(2) --> [batch_sizse, embedding_size, 1]
    # torch.bmm().squeeze() --> [batch_size, neg_sampling_size]
    neg_score = torch.bmm(neg_embed_v, embed_u.unsqueeze(2))
    neg_score = neg_score.view(batch_size, neg_sampling_size)
    neg_score = torch.sum(neg_score, dim=1)
    sum_log_sampled = F.logsigmoid(-1*neg_score).squeeze()

    loss = log_target + sum_log_sampled

    return -1*loss.sum()/batch_size

  def get_embeddings(self, total_num, batch_size=32, permutate_sz=1):
      res = []
      for i in range(permutate_sz):
          idx = 0
          embeddings = []
          with torch.no_grad():
              while idx < total_num:
                  idxs = np.array([i for i in range(idx, min(idx+batch_size, total_num))])
                  u_pos, _ = self.dataset_sampler.get_batch(idxs)
                  embeddings.append(self.u_embeddings(self.enc(u_pos, u=True)).cpu().numpy())
                  # embeddings.append(self.enc(u_pos, u=True).cpu().numpy())
                  idx += batch_size

          embeddings =np.concatenate(embeddings, axis=0)
          res.append(embeddings)
      # print(np.mean(res, axis=0).shape)
      # input()
      return np.mean(res, axis=0)

if __name__ == '__main__':
    print('getting batch ...')
    print(dataset_sampler.get_batch(torch.from_numpy(np.array([1]))))
    print()
