from typing import Literal

import polars as pl
import xarray as xr
from omegaconf import DictConfig

from src.data.validation import split_validation
from src.utils import convert_csv_to_parquet
from src.utils.competition_utils import multiply_old_factor, shrink_memory


class DataProvider:
    def __init__(self, config: DictConfig):
        self.config = config
        self.all_target_cols = pl.read_parquet(config.input_path / "sample_submission.parquet", n_rows=1).columns[1:]

    def load_data(self) -> tuple[pl.DataFrame, pl.DataFrame]:
        train_df, test_df = self._load_data()
        train_df = train_df.with_columns(grid_id=(pl.col("sample_id") % 384), time_id=pl.col("sample_id") // 384)
        if self.config.task_type == "grid_pred":
            train_df = train_df.drop(self.all_target_cols)

        train_df = self._downsample(train_df, self.config.run_mode)
        if self.config.task_type == "main" and self.config.use_grid_feat:
            use_cols = ["lat", "lon"]
            train_df, test_df = self._merge_grid_feat(train_df, test_df, use_cols)

        train_df = split_validation(self.config, train_df)

        if self.config.task_type == "main" and self.config.mul_old_factor:
            train_df = multiply_old_factor(self.config.input_path, train_df)

        train_df = train_df.drop(["time_id"])
        return train_df, test_df

    def _load_data(self):
        if not (self.config.input_path / "train_shrinked.parquet").exists():
            if not (self.config.input_path / "train.parquet").exists():
                convert_csv_to_parquet(self.config.input_path / "train.csv", delete_csv=True)
            train_df = pl.read_parquet(self.config.input_path / "train.parquet")
            train_df = shrink_memory(train_df, refer_df=None)
            train_df.write_parquet(self.config.input_path / "train_shrinked.parquet")
            (self.config.input_path / "train.parquet").unlink()
        else:
            train_df = pl.read_parquet(self.config.input_path / "train_shrinked.parquet")

        if not (self.config.input_path / "test_shrinked.parquet").exists():
            if not (self.config.input_path / "test.parquet").exists():
                convert_csv_to_parquet(self.config.input_path / "test.csv", delete_csv=True)
            test_df = pl.read_parquet(self.config.input_path / "test.parquet")
            test_df = shrink_memory(test_df, refer_df=train_df)
            test_df.write_parquet(self.config.input_path / "test_shrinked.parquet")
            (self.config.input_path / "test.parquet").unlink()
        else:
            test_df = pl.read_parquet(self.config.input_path / "test_shrinked.parquet")

        if not (self.config.input_path / "sample_submission.parquet").exists():
            convert_csv_to_parquet(self.config.input_path / "sample_submission.csv", delete_csv=True)
        if not (self.config.input_path / "sample_submission_old.parquet").exists():
            convert_csv_to_parquet(self.config.input_path / "sample_submission_old.csv", delete_csv=True)
        return train_df, test_df

    def _downsample(self, train_df: pl.DataFrame, run_mode: Literal["full", "dev", "debug"]):
        time_ids = sorted(train_df["time_id"].unique())
        num_ids = 100 if run_mode == "debug" else 9000 if run_mode == "dev" else len(time_ids)
        use_ids = time_ids[-num_ids:]
        train_df = train_df.filter(pl.col("time_id").is_in(use_ids))
        return train_df

    def _merge_grid_feat(
        self,
        train_df: pl.DataFrame,
        test_df: pl.DataFrame,
        use_cols: list[str] | None,
    ):
        # Need to predict grid_id for test data
        test_grid_id = pl.read_parquet(self.config.add_path / "test_grid_id.parquet")
        test_df = test_df.join(test_grid_id, on="sample_id", how="left")

        grid_feat = self._load_grid_feat(use_cols=use_cols)
        train_df = train_df.join(grid_feat, on=["grid_id"], how="left")
        test_df = test_df.join(grid_feat, on=["grid_id"], how="left")
        return train_df, test_df

    def _load_grid_feat(self, use_cols: list[str] | None = None):
        feat_path = self.config.add_path / "grid_feat.parquet"
        if feat_path.exists():
            grid_feat = pl.read_parquet(feat_path)
        else:
            grid_info_path = self.config.add_path / "ClimSim_low-res_grid-info.nc"
            grid_info = xr.open_dataset(grid_info_path)
            grid_info = pl.from_pandas(grid_info.to_dataframe().reset_index())
            grid_info = grid_info.rename({"ncol": "grid_id"})
            grid_feat = grid_info.group_by("grid_id").agg(
                [pl.col(col).mean() for col in grid_info.columns if col not in ["grid_id", "time"]]
            )
            grid_feat = grid_feat.with_columns(grid_id=pl.col("grid_id").cast(pl.Int32))
            grid_feat.write_parquet(feat_path)

        if use_cols is not None:
            grid_feat = grid_feat.select(["grid_id"] + use_cols)
        return grid_feat
