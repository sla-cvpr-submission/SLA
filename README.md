# Source Label Adaptation
CVPR 2023 SUBMISSION - Semi-Supervised Domain Adaptation with Source Label Adaptation

## Installation

`pip install -r requirements`

## Usage

1. Dataset Preparement
    
    Following [MME](https://github.com/VisionLearningGroup/SSDA_MME) to download the dataset and the split files.

    In `config.yaml`, specify the path for the dataset, and the path for the split files.
    - all: the file with all samples.
    - 1shot:
        - train: training split for 1-shot setting.
        - test: test split for 1-shot setting.
    - 3shot:
        - train: training split for 3-shot setting.
        - test: test split for 3-shot setting.
    - val: validation split.

2. Running

Take MME + SLA on 3-shot A -> C Office-Home dataset for example:

```
python --method MME_LC --source 0 --target 1 --seed 1102 --num_iters 10000 --shot 3shot --alpha 0.3 --update_interval 500 --warmup 500 --T 0.6
```
