import torch.nn.functional as F
from torch import nn

from src.model.models.base import BaseModel
from src.model.modules.conv_extractor import ConvExtractor
from src.model.modules.lstm_block import LSTMBlock
from src.model.modules.positional_encoding import VerticalEncoding


class LEAPTransformer(BaseModel):
    def __init__(
        self,
        in_dim: int,
        out_dim: int,
        hidden_dim: int = 256,
        trans_num_layers: int = 4,
        lstm_block_num: int = 2,
        scaler_num: int = 16,
        multi_task: bool = False,
    ):
        super().__init__()
        if multi_task:
            out_dim = out_dim * 3

        self.scaler_num = scaler_num
        self.conv_extractor = ConvExtractor(in_dim, hidden_dim)
        self.pe = VerticalEncoding(hidden_dim, learnable=True)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden_dim,
            nhead=8,
            dim_feedforward=hidden_dim * 4,
            dropout=0.0,
            activation=F.gelu,
            batch_first=True,
            norm_first=False,
        )
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=trans_num_layers)
        self.lstm_blocks = nn.ModuleList([LSTMBlock(hidden_dim, scaler_num) for _ in range(lstm_block_num)])
        self.head = nn.Sequential(nn.Linear(hidden_dim, 64), nn.LayerNorm([60, 64]), nn.ReLU(), nn.Linear(64, out_dim))

    def forward(self, x):
        s_x = x[:, 0, -self.scaler_num :]
        x = super().forward(x)
        x = self.conv_extractor(x)
        x = self.pe(x)
        x = self.transformer_encoder(x)
        for i in range(len(self.lstm_blocks)):
            x = self.lstm_blocks[i](x, s_x)
        x = self.head(x)
        return x
