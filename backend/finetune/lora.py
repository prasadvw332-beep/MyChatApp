"""Fine-tune the chat model with LoRA (QLoRA), then run it in Ollama.

    uv add --group finetune transformers peft trl datasets bitsandbytes accelerate
    uv run python -m finetune.lora --data finetune/data.jsonl --base Qwen/Qwen2.5-1.5B-Instruct

When fine-tuning beats RAG, and when it doesn't:
    RAG          for KNOWLEDGE: facts that change, must be cited, or are permission-controlled. A fine-tuned
                 model can't cite, can't forget a deleted document, and goes stale on the day you train it.
    fine-tuning  for BEHAVIOUR: a format, tone, or domain language the prompt can't get reliably; a small
                 model doing one narrow job as well as a large one (cheaper, faster); fewer prompt tokens.
Most teams need RAG first and fine-tuning rarely; the two combine well (a model tuned to answer from
context in your house style, fed by RAG).

LoRA: instead of updating all of the model's billions of weights, train two small matrices per layer
(rank r, here 16) whose product is added to the frozen weights: ~1% of the parameters, so it fits on
one GPU and the result is a small "adapter" file. QLoRA: the frozen base is loaded in 4 bits, which
cuts memory again (a 7B model trains on a 24 GB GPU).

The examples teach the answer *format* the app wants (answer from the context, cite the file), using
the same system prompt as production, so the model learns to behave well inside our real prompt.

Into Ollama afterwards:
    1. merge the adapter into the base (--merge does it), 2. convert to GGUF with llama.cpp
       (python convert_hf_to_gguf.py finetune/lora-merged --outtype q8_0), 3. a Modelfile with
       "FROM ./model.gguf", then `ollama create mychat-tuned -f Modelfile`, and CHAT_MODEL=mychat-tuned.
Then run the evals (evals/compare.py) against the base model before switching.
"""

import argparse
import json
from pathlib import Path

from app.prompts.rag import RAG_PROMPT


def to_chat(row: dict) -> dict:
    """One training example in the chat format the tokenizer's template expects."""
    context = f'<document source="{row.get("source")}">\n{row["chunk"]}\n</document>'
    return {"messages": [
        {"role": "system", "content": RAG_PROMPT.format(context=context)},
        {"role": "user", "content": row["question"]},
        {"role": "assistant", "content": f"{row['answer']} [{row.get('source')}]"},
    ]}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("finetune/data.jsonl"))
    p.add_argument("--base", default="Qwen/Qwen2.5-1.5B-Instruct")
    p.add_argument("--out", default="finetune/lora-adapter")
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--merge", action="store_true", help="also save base + adapter merged, for GGUF conversion")
    args = p.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    tokenizer = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        quantization_config=BitsAndBytesConfig(  # QLoRA: the frozen base in 4-bit NF4
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        ),
        device_map="auto",
    )
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=Dataset.from_list([to_chat(r) for r in rows]),
        peft_config=LoraConfig(
            r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        ),
        args=SFTConfig(
            output_dir=args.out, num_train_epochs=args.epochs, per_device_train_batch_size=4,
            gradient_accumulation_steps=4, learning_rate=2e-4, lr_scheduler_type="cosine", warmup_ratio=0.03,
            logging_steps=10, save_strategy="no", bf16=True,
            assistant_only_loss=True,  # learn to write the answers, not to repeat the prompts
        ),
    )
    trainer.train()
    trainer.save_model(args.out)
    print(f"adapter saved to {args.out}")
    if args.merge:
        from peft import AutoPeftModelForCausalLM
        merged = AutoPeftModelForCausalLM.from_pretrained(args.out, torch_dtype=torch.bfloat16).merge_and_unload()
        merged.save_pretrained("finetune/lora-merged")
        tokenizer.save_pretrained("finetune/lora-merged")
        print("merged model saved to finetune/lora-merged: convert it to GGUF for Ollama (see the docstring)")


if __name__ == "__main__":
    main()
