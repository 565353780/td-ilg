import torch
import torch.nn as nn
import torch.nn.functional as F

from td_ilg.Model.gpt import GPT
from td_ilg.Method.model import sample
from td_ilg.Config.cfg import _cfg


class ClassEncoder(nn.Module):
    """Container module with an encoder, a recurrent or transformer module, and a decoder."""

    def __init__(
        self,
        ninp,
        nhead,
        nlayers,
        nclasses=55,
        coord_vocab_size=256,
        latent_vocab_size=512,
        reso=128,
    ):
        super(ClassEncoder, self).__init__()
        self.reso = reso

        self.pos_emb = nn.Parameter(nn.Embedding(reso, ninp).weight[None])

        self.x_tok_emb = nn.Embedding(coord_vocab_size, ninp)
        self.y_tok_emb = nn.Embedding(coord_vocab_size, ninp)
        self.z_tok_emb = nn.Embedding(coord_vocab_size, ninp)

        self.latent_tok_emb = nn.Embedding(latent_vocab_size, ninp)

        self.coord_vocab_size = coord_vocab_size

        self.latent_vocab_size = latent_vocab_size

        self.class_enc = nn.Embedding(nclasses, ninp)

        self.transformer = GPT(
            vocab_size=512,
            block_size=self.reso,
            n_layer=nlayers,
            n_head=nhead,
            n_embd=ninp,
            embd_pdrop=0.1,
            resid_pdrop=0.1,
            attn_pdrop=0.1,
        )

        self.ln_x = nn.LayerNorm(ninp)
        self.x_head = nn.Linear(ninp, coord_vocab_size, bias=False)

        self.ln_y = nn.LayerNorm(ninp)
        self.y_head = nn.Linear(ninp, coord_vocab_size, bias=False)

        self.ln_z = nn.LayerNorm(ninp)
        self.z_head = nn.Linear(ninp, coord_vocab_size, bias=False)

        self.ln_latent = nn.LayerNorm(ninp)
        self.latent_head = nn.Linear(ninp, latent_vocab_size, bias=False)

        self.default_cfg = _cfg()
        return

    def forward(self, coordinates, latents, classes):
        features = self.class_enc(classes)[:, None]  # B x 1 x C

        position_embeddings = self.pos_emb  # 1 x S x C

        x_token_embeddings = self.x_tok_emb(coordinates[:, :, 0])  # B x S x C
        y_token_embeddings = self.y_tok_emb(coordinates[:, :, 1])  # B x S x C
        z_token_embeddings = self.z_tok_emb(coordinates[:, :, 2])  # B x S x C
        latent_token_embeddings = self.latent_tok_emb(latents)  # B x S x C

        token_embeddings = torch.cat(
            [
                features,
                latent_token_embeddings
                + x_token_embeddings
                + y_token_embeddings
                + z_token_embeddings,
            ],
            dim=1,
        )  # B x (1+S) x C
        embeddings = token_embeddings[:, :-1] + position_embeddings  # B x S x C

        x = self.transformer.drop(embeddings)

        for block in self.transformer.blocks[:12]:
            x = block(x)  # B x S x C
        x_logits = (
            F.log_softmax(self.x_head(self.ln_x(x)), dim=-1)
            .permute(0, 2, 1)
            .view(coordinates.shape[0], self.coord_vocab_size, self.reso)
        )
        x = x + x_token_embeddings + position_embeddings

        for block in self.transformer.blocks[12:16]:
            x = block(x)
        y_logits = (
            F.log_softmax(self.y_head(self.ln_y(x)), dim=-1)
            .permute(0, 2, 1)
            .view(coordinates.shape[0], self.coord_vocab_size, self.reso)
        )
        x = x + x_token_embeddings + y_token_embeddings + position_embeddings

        for block in self.transformer.blocks[16:20]:
            x = block(x)
        z_logits = (
            F.log_softmax(self.z_head(self.ln_z(x)), dim=-1)
            .permute(0, 2, 1)
            .view(coordinates.shape[0], self.coord_vocab_size, self.reso)
        )
        x = (
            x
            + x_token_embeddings
            + y_token_embeddings
            + z_token_embeddings
            + position_embeddings
        )

        for block in self.transformer.blocks[20:]:
            x = block(x)
        latent_logits = (
            F.log_softmax(self.latent_head(self.ln_latent(x)), dim=-1)
            .permute(0, 2, 1)
            .view(coordinates.shape[0], self.latent_vocab_size, self.reso)
        )

        return x_logits, y_logits, z_logits, latent_logits

    @torch.no_grad()
    def sample(self, cond):
        cond = cond[:, None]

        position_embeddings = self.pos_emb
        print("sample: cond:", cond.shape, cond.dtype)
        print(
            "sample: position_embeddings:",
            position_embeddings.shape,
            position_embeddings.dtype,
        )

        coord1, coord2, coord3, latent = None, None, None, None
        for i in range(self.reso):
            if coord1 is None:
                x = self.transformer.drop(cond + position_embeddings[:, :1, :])
                print("sample x1:", x.shape)
                for block in self.transformer.blocks[:12]:
                    x = block(x)  # B x S x C
                print("sample x2:", x.shape)
                coord1_logits = self.x_head(self.ln_x(x))
                print("sample coord1_logits:", coord1_logits.shape)
                ix = sample(coord1_logits)
                print("sample ix:", ix.shape)
                print("ix:", ix)
                coord1 = ix
                print("sample coord1:", coord1.shape)
                x_token_embeddings = self.x_tok_emb(coord1)
                print(
                    "sample x_token_embeddings:",
                    x_token_embeddings.shape,
                    x_token_embeddings.dtype,
                )

                x = x + x_token_embeddings + position_embeddings[:, :1, :]
                for block in self.transformer.blocks[12:16]:
                    x = block(x)  # B x S x C
                coord2_logits = self.y_head(self.ln_y(x))
                ix = sample(coord2_logits)
                coord2 = ix
                y_token_embeddings = self.y_tok_emb(coord2)

                x = (
                    x
                    + x_token_embeddings
                    + y_token_embeddings
                    + position_embeddings[:, :1, :]
                )
                for block in self.transformer.blocks[16:20]:
                    x = block(x)  # B x S x C
                coord3_logits = self.z_head(self.ln_z(x))
                ix = sample(coord3_logits)
                coord3 = ix
                z_token_embeddings = self.z_tok_emb(coord3)

                x = (
                    x
                    + x_token_embeddings
                    + y_token_embeddings
                    + z_token_embeddings
                    + position_embeddings[:, :1, :]
                )
                for block in self.transformer.blocks[20:]:
                    x = block(x)  # B x S x C
                latent_logits = self.latent_head(self.ln_latent(x))
                ix = sample(latent_logits)
                latent = ix
                print("sample latent_logits:", latent_logits.shape, latent_logits.dtype)

            else:
                x_token_embeddings = self.x_tok_emb(coord1)  # B x S x C
                y_token_embeddings = self.y_tok_emb(coord2)  # B x S x C
                z_token_embeddings = self.z_tok_emb(coord3)  # B x S x C
                latent_token_embeddings = self.latent_tok_emb(latent)  # B x S x C
                print("latent_token_embeddings:", latent_token_embeddings.shape)

                token_embeddings = torch.cat(
                    [
                        cond,
                        latent_token_embeddings
                        + x_token_embeddings
                        + y_token_embeddings
                        + z_token_embeddings,
                    ],
                    dim=1,
                )  # B x (1+S) x C
                embeddings = (
                    token_embeddings
                    + position_embeddings[:, : token_embeddings.shape[1], :]
                )  # B x S x C

                x = self.transformer.drop(embeddings)
                for block in self.transformer.blocks[:12]:
                    x = block(x)  # B x S x C
                coord1_logits = self.x_head(self.ln_x(x))
                ix = sample(coord1_logits)
                coord1 = torch.cat((coord1, ix), dim=1)
                x_token_embeddings = self.x_tok_emb(coord1)

                x = x + x_token_embeddings + position_embeddings[:, : x.shape[1], :]
                for block in self.transformer.blocks[12:16]:
                    x = block(x)  # B x S x C
                coord2_logits = self.y_head(self.ln_y(x))
                ix = sample(coord2_logits)
                coord2 = torch.cat((coord2, ix), dim=1)
                y_token_embeddings = self.y_tok_emb(coord2)

                x = (
                    x
                    + x_token_embeddings
                    + y_token_embeddings
                    + position_embeddings[:, : x.shape[1], :]
                )
                for block in self.transformer.blocks[16:20]:
                    x = block(x)  # B x S x C
                coord3_logits = self.z_head(self.ln_z(x))
                ix = sample(coord3_logits)
                coord3 = torch.cat((coord3, ix), dim=1)
                z_token_embeddings = self.z_tok_emb(coord3)

                x = (
                    x
                    + x_token_embeddings
                    + y_token_embeddings
                    + z_token_embeddings
                    + position_embeddings[:, : x.shape[1], :]
                )
                for block in self.transformer.blocks[20:]:
                    x = block(x)  # B x S x C
                latent_logits = self.latent_head(self.ln_latent(x))
                ix = sample(latent_logits)
                latent = torch.cat((latent, ix), dim=1)
        return coord1, coord2, coord3, latent

    @torch.jit.ignore
    def no_weight_decay(self):
        return {"pos_emb", "xyz_emb"}


def class_encoder_55_512_1024_24_K1024():
    return ClassEncoder(
        ninp=1024,
        nhead=16,
        nlayers=24,
        nclasses=55,
        coord_vocab_size=256,
        latent_vocab_size=1024,
        reso=512,
    )
