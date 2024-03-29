import os
import torch
from math import sqrt, ceil
from tqdm import tqdm
from typing import Union

from data_convert.Method.data import toData

from a_sdf.Data.mesh import Mesh
from a_sdf.Model.asdf_model import ASDFModel
from a_sdf.Method.pcd import getPointCloud
from a_sdf.Method.render import renderGeometries

from td_ilg.Model.asdf_autoencoder import ASDFAutoEncoder


class ASDFAutoEncoderSampler(object):
    def __init__(
        self, model_file_path: Union[str, None] = None, device: str = "cpu"
    ) -> None:
        self.asdf_channel = 100
        self.sh_2d_degree = 3
        self.sh_3d_degree = 4
        self.hidden_dim = 512
        self.sample_direction_num = 400
        self.direction_upscale = 4

        self.device = device

        self.model = ASDFAutoEncoder(
            asdf_channel=self.asdf_channel,
            sh_2d_degree=self.sh_2d_degree,
            sh_3d_degree=self.sh_3d_degree,
            hidden_dim=self.hidden_dim,
            dtype=torch.float32,
            device=self.device,
            sample_direction_num=self.sample_direction_num,
            direction_upscale=self.direction_upscale,
        ).to(self.device)

        if model_file_path is not None:
            self.loadModel(model_file_path)
        return

    def toInitialASDFModel(self) -> ASDFModel:
        asdf_model = ASDFModel(
            max_sh_3d_degree=self.sh_3d_degree,
            max_sh_2d_degree=self.sh_2d_degree,
            use_inv=False,
            dtype=torch.float32,
            device="cpu",
            sample_direction_num=self.sample_direction_num,
            direction_upscale=self.direction_upscale,
        )

        return asdf_model

    def loadModel(self, model_file_path, resume_model_only=True):
        if not os.path.exists(model_file_path):
            print("[ERROR][ASDFAutoEncoder::loadModel]")
            print("\t model_file not exist!")
            return False

        model_dict = torch.load(model_file_path)

        self.model.load_state_dict(model_dict["model"])

        if not resume_model_only:
            self.optimizer.load_state_dict(model_dict["optimizer"])
            self.step = model_dict["step"]
            self.eval_step = model_dict["eval_step"]
            self.loss_min = model_dict["loss_min"]
            self.eval_loss_min = model_dict["eval_loss_min"]
            self.log_folder_name = model_dict["log_folder_name"]

        print("[INFO][ASDFAutoEncoderSampler::loadModel]")
        print("\t load model success!")
        return True

    @torch.no_grad()
    def sample(
        self, mesh_file_path_list: list, sample_point_num: int, rad_density: int
    ) -> bool:
        self.model.eval()

        object_dist = [2, 0, 2]
        mesh_translate = [1, 0, 0]

        row_num = ceil(sqrt(len(mesh_file_path_list)))

        mesh_list = []
        asdf_pcd_list = []

        self.model.rad_density = rad_density

        for i in tqdm(range(len(mesh_file_path_list))):
            mesh_file_path = mesh_file_path_list[i]

            if not os.path.exists(mesh_file_path):
                print("[WARN][ASDFAutoEncoderSampler::sample]")
                print("\t mesh file not exist!")
                continue

            mesh = Mesh(mesh_file_path)
            mesh.samplePoints(sample_point_num)

            points = toData(mesh.sample_pts, "torch",
                            torch.float32).to(self.device)
            points = points.reshape(1, sample_point_num, 3)

            asdf_points = toData(self.model(points), "numpy").reshape(-1, 3)
            pcd = getPointCloud(asdf_points)

            translate = [int(i / row_num) * object_dist[0], 0 * object_dist[1], (i % row_num) * object_dist[2]]

            o3d_mesh = mesh.toO3DMesh()
            o3d_mesh.translate(translate)
            o3d_mesh.translate(mesh_translate)
            pcd.translate(translate)
            mesh_list.append(o3d_mesh)
            asdf_pcd_list.append(pcd)

        renderGeometries(mesh_list + asdf_pcd_list, "asdf point cloud")
        return True
