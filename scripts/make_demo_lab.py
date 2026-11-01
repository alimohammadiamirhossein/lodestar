"""Build the small demo tree used in the README: a repo, two image datasets, 30 runs, a paper.

    python scripts/make_demo_lab.py /tmp/lab-demo
    lodestar index /tmp/lab-demo && lodestar find train loader
"""

import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "lab-demo")


def put(rel: str, size: int = 1) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


for rel in ("train.py", "models/unet.py", "data/train_loader.py", "eval/fid.py",
            "configs/base.yaml", ".git/HEAD"):
    put(f"diffusion-lab/{rel}", 400)
for i in range(1500):
    put(f"datasets/celeba/img_{i:06d}.jpg", 300)
for c in range(120):
    for j in range(4):
        put(f"datasets/imagenet-val/n{c:08d}/v_{j}.JPEG", 200)
for i in range(30):
    put(f"runs/ablation_{i:02d}/events.out.tfevents.{i}", 100)
    put(f"runs/ablation_{i:02d}/ckpt/model_final.pt", 5000)
put("papers/related_work/survey.pdf", 800)
print(f"built {root}")
