#!/usr/bin/python3
from absl import flags
import os
import sys
from etils import epath
import jax
from jax.experimental import maps
from jax.experimental.pjit import PartitionSpec
from jax.experimental.pjit import pjit
from jax.experimental import multihost_utils
import numpy as np
import orbax.checkpoint as orbax
import time
from google.cloud import storage
from jax._src.lib import xla_bridge
from jax._src.cloud_tpu_init import running_in_cloud_tpu_vm, get_metadata

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "0"
os.environ["JAX_USE_PJRT_C_API_ON_TPU"] = "1"

print(
    f"orbax_async_checkpoint_test: Devices found. number {len(jax.devices())}, details: {jax.devices()}",
    flush=True,
)

FLAGS = flags.FLAGS

bucket_path_flag = flags.DEFINE_string(
    "bucket_path", None, "GCS bucket path, e.g. gs://my-bucket")

dir_name_flag = flags.DEFINE_string(
    "ckpt_dir", None, "GCS cloud bucket directory")

FLAGS(sys.argv)  # parses the flags.

print('orbax_async_checkpoint_test: Setting up ckpt_dir', flush=True)
bucket_path = epath.Path(bucket_path_flag.value)
dir_name = dir_name_flag.value
ckpt_dir = bucket_path / dir_name
print('orbax_async_checkpoint_test: Using ckpt_dir: ', ckpt_dir, flush=True)

def gen_local_ip():
  # array with 1 element, we just grab it.
  return get_metadata('worker-network-endpoints').split(',')[0] 

def gen_local_ip_nums():
  return [int(num) for num in gen_local_ip().split(":")[-1].split(".")]

def get_coordinator_ip():
  local_ip_nums = jax.numpy.array(gen_local_ip_nums())
  coordinator_ip_nums = multihost_utils.broadcast_one_to_all(local_ip_nums)
  coordinator_ip_strings = [str(num) for num in list(coordinator_ip_nums)]
  return '.'.join(coordinator_ip_strings)

port = "8476"
coordinator_address = get_coordinator_ip() + ":" + port
print('orbax_async_checkpoint_test: Using synced coordinator_address: ', coordinator_address, flush=True)

jax.distributed.initialize(coordinator_address=coordinator_address,num_processes=jax.process_count(),process_id=jax.process_index())
print('orbax_async_checkpoint_test: Initialized successful!', flush=True)

mngr = orbax.CheckpointManager(ckpt_dir, orbax.AsyncCheckpointer(orbax.PyTreeCheckpointHandler()))
print("orbax_async_checkpoint_test: Created Async CheckpointManager", flush=True)

BATCH = 512
SIZE = 128

def gen_data():
  return jax.process_index() * jax.numpy.ones(
      (BATCH, SIZE, SIZE), dtype=jax.numpy.bfloat16)

# Define array lengths based on device count
num_devices = len(jax.devices())
mesh_shape = [len(jax.devices())]
devices = np.asarray(jax.devices()).reshape(*mesh_shape)
mesh = maps.Mesh(devices, ("x"))

gen_data_sharded = pjit(
    gen_data, in_axis_resources=None, out_axis_resources=PartitionSpec("x")
)

with maps.Mesh(mesh.devices, mesh.axis_names):
  presharded_X = jax.block_until_ready(gen_data_sharded())
  to_save = {"my_X": presharded_X}
  print("orbax_async_checkpoint_test: Attempting save...", flush=True)
  mngr.save(0, to_save)
  print("orbax_async_checkpoint_test: Save successful!", flush=True)

# Try to restore now
print("orbax_async_checkpoint_test: Attempting restore...!", flush=True)
s2 = mngr.restore(0)
print("orbax_async_checkpoint_test: Restore successful!", flush=True)

print("orbax_async_checkpoint_test: Test finished successfully", flush=True)
