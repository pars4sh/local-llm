#!/usr/bin/env python3
"""Check the vLLM environment on a GPU node. Run inside a short SLURM job after any version change.

Prints the versions that matter, then checks that:
- torch sees the GPU with the expected compute capability (default 12.0, sm_120)
- vllm and its compiled kernels import
- a Triton kernel and a FlashInfer JIT kernel compile and run on this GPU

Exits non-zero if a required check fails. FlashInfer is reported but not required.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import os
import shutil
import sys
import time

PACKAGES = (
    "vllm",
    "torch",
    "torchvision",
    "transformers",
    "flashinfer-python",
    "triton",
    "nvidia-cuda-runtime",
    "nvidia-cuda-nvcc",
)


def check(label: str, fn, required: bool = True) -> bool:
    t0 = time.monotonic()
    try:
        detail = fn()
    except Exception as e:  # noqa: BLE001 - report every failure the same way
        state = "FAIL" if required else "WARN"
        print(f"[{state}] {label}: {type(e).__name__}: {e}", flush=True)
        return not required
    print(
        f"[ OK ] {label} ({time.monotonic() - t0:.1f}s){': ' + detail if detail else ''}"
    )
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--capability", default="12.0", help="expected compute capability")
    args = p.parse_args()
    want = tuple(int(x) for x in args.capability.split("."))

    print(f"python {sys.version.split()[0]} at {sys.executable}")
    for name in PACKAGES:
        try:
            print(f"{name:22} {md.version(name)}")
        except md.PackageNotFoundError:
            print(f"{name:22} (not installed)")
    print(f"CUDA_HOME={os.environ.get('CUDA_HOME', '<unset>')}")
    print(f"nvcc={shutil.which('nvcc')} ninja={shutil.which('ninja')}", flush=True)

    import torch

    def cuda():
        assert torch.cuda.is_available(), "torch.cuda.is_available() is False"
        cap = torch.cuda.get_device_capability()
        free, total = torch.cuda.mem_get_info()
        assert cap == want, f"capability {cap}, expected {want}"
        return (
            f"{torch.cuda.get_device_name()} capability={cap} "
            f"torch.version.cuda={torch.version.cuda} "
            f"free/total={free / 2**30:.1f}/{total / 2**30:.1f} GiB"
        )

    def matmul():
        a = torch.randn(4096, 4096, device="cuda", dtype=torch.bfloat16)
        a @ a  # warm up cuBLAS
        torch.cuda.synchronize()
        t0 = time.monotonic()
        for _ in range(20):
            a @ a
        torch.cuda.synchronize()
        tflops = 20 * 2 * 4096**3 / (time.monotonic() - t0) / 1e12
        return f"bf16 4096^3 ~{tflops:.0f} TFLOP/s"

    def vllm_import():
        import vllm
        import vllm._C_stable_libtorch
        from vllm.platforms import current_platform

        return f"vllm {vllm.__version__}, platform={current_platform.device_name}"

    def triton_kernel():
        import triton
        import triton.language as tl

        @triton.jit
        def add(x, y, out, n, BLOCK: tl.constexpr):
            i = tl.program_id(0) * BLOCK + tl.arange(0, BLOCK)
            m = i < n
            tl.store(out + i, tl.load(x + i, mask=m) + tl.load(y + i, mask=m), mask=m)

        x = torch.rand(10_000, device="cuda")
        out = torch.empty_like(x)
        add[(triton.cdiv(x.numel(), 1024),)](x, x, out, x.numel(), BLOCK=1024)
        assert torch.allclose(out, 2 * x)
        return f"triton {triton.__version__}"

    def flashinfer_kernel():
        from vllm.utils.flashinfer import has_flashinfer

        assert has_flashinfer(), "vllm reports FlashInfer disabled"
        import flashinfer

        probs = torch.softmax(torch.randn(4, 32_000, device="cuda"), dim=-1)
        ids = flashinfer.sampling.top_k_top_p_sampling_from_probs(probs, 50, 0.9)
        assert ids.shape == (4,)
        return "JIT sampling kernel compiled and ran"

    ok = check("torch CUDA device", cuda)
    ok &= check("bf16 matmul", matmul)
    ok &= check("import vllm and its kernels", vllm_import)
    ok &= check("Triton kernel", triton_kernel)
    check("FlashInfer kernel", flashinfer_kernel, required=False)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
