import random
from typing import List, Dict, Tuple, Any

import torch
import torch.nn.functional as F
from torch.utils.data import Dataset
from transformers import AutoTokenizer
from tqdm import tqdm


TEMPLATES = {
    "causal_newlines": {
        "with_input": [
            ("{instruction}\nВход: {inp}\nВыход: ", "{out}"),
            ("{instruction}\n\nВход: {inp}\n\nОтвет: ", "{out}"),
            ("Задание: {instruction}\nВход: {inp}\nВыход: ", "{out}"),
            ("Инструкция: {instruction}\nДано: {inp}\nВыход: ", "{out}"),
            ("{instruction}\n\n{inp}\n\nОтвет: ", "{out}"),
            ("{instruction}\n\n{inp}\n\n", "{out}"),
            ("{instruction}\n{inp}\n\n", "{out}"),
            ("{instruction}\n{inp}\n", "{out}"),
            ("Задание: {instruction}\n\n{inp}\n\n", "{out}"),
        ],
        "no_input": [
            ("{instruction} Ответ: ", "{out}"),
            ("{instruction} Выход: ", "{out}"),
            ("{instruction}\nВыход: ", "{out}"),
            ("{instruction}\n\nОтвет: ", "{out}"),
            ("{instruction}\n", "{out}"),
            ("{instruction}\n\n", "{out}"),
            ("Задание: {instruction}\n\n", "{out}"),
            ("Инструкция: {instruction}\n\n", "{out}"),
        ],
    },
    "seq2seq_no_newlines": {
        "with_input": [
            ("{instruction} | Вход: {inp}", "{out}"),
            ("Задание: {instruction} | Вход: {inp}", "{out}"),
            ("Инструкция: {instruction} - Дано: {inp}", "{out}"),
            ("{instruction} | Вход: {inp}", "{out}"),
        ],
        "no_input": [
            ("{instruction}", "{out}"),
            ("Задание: {instruction}", "{out}"),
            ("Инструкция: {instruction}", "{out}"),
        ]
    }
}


class InstructDataset(Dataset):
    def __init__(
        self,
        original_records: List[Dict],
        tokenizer: AutoTokenizer,
        max_source_tokens_count: int,
        max_target_tokens_count: int,
        template_category: str,
        sample_rate: float = 1.0,
        only_target_loss: bool = True,
        input_type: str = "causal",
        target_field: str = "output",
        source_field: str = "input"
    ):
        self.original_records = original_records
        self.sample_rate = sample_rate
        self.tokenizer = tokenizer
        self.max_source_tokens_count = max_source_tokens_count
        self.max_target_tokens_count = max_target_tokens_count
        self.only_target_loss = only_target_loss
        self.input_type = input_type
        self.template_category = template_category
        self.target_field = target_field
        self.source_field = source_field
        self.is_printed = False

        self.records = []
        for record in tqdm(original_records):
            if random.random() > self.sample_rate:
                continue
            tensors = self.convert_record(record)
            self.records.append(tensors)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        return self.records[index]

    def convert_record(self, record):
        instruction = record["instruction"]
        inp = record[self.source_field]
        out = record[self.target_field]
        if inp.strip() != "":
            templates = TEMPLATES[self.template_category]["with_input"]
            prompt_template, completion_template = random.choice(templates)
            source = prompt_template.format(instruction=instruction.strip(), inp=inp.strip())
        else:
            templates = TEMPLATES[self.template_category]["no_input"]
            prompt_template, completion_template = random.choice(templates)
            source = prompt_template.format(instruction=instruction.strip())
        target = completion_template.format(out=out.strip()).strip()
        if not self.is_printed:
            print("SOURCE:")
            print(source)
            print("TARGET:")
            print(target)
            self.is_printed = True
        if self.input_type == "causal":
            return self.convert_causal(source, target)
        elif self.input_type == "seq2seq":
            return self.convert_seq2seq(source, target)
        else:
            assert False

    def convert_causal(self, source, target=None):
        source_tokens = self.tokenizer(
            source,
            add_special_tokens=False,
            max_length=self.max_source_tokens_count,
            padding=False,
            truncation=True
        )["input_ids"]
        if self.tokenizer.bos_token_id:
            source_tokens.insert(0, self.tokenizer.bos_token_id)
        input_ids = source_tokens[:]
        actual_length = len(input_ids)
        max_length = self.max_source_tokens_count + self.max_target_tokens_count + 2
        if target is not None:
            target_tokens = self.tokenizer(
                target,
                add_special_tokens=False,
                max_length=self.max_target_tokens_count,
                padding=False,
                truncation=True
            )["input_ids"]
            input_ids += target_tokens + [self.tokenizer.eos_token_id]
            actual_length = len(input_ids)
            padding = [self.tokenizer.pad_token_id for i in range(len(input_ids), max_length)]
            input_ids.extend(padding)

        input_ids = torch.LongTensor(input_ids)
        labels = input_ids.clone()
        attention_mask = input_ids.new_ones(input_ids.size())
        labels[actual_length:] = -100
        attention_mask[actual_length:] = 0
        if self.only_target_loss:
            labels[:len(source_tokens)] = -100
        assert input_ids.size(0) == labels.size(0) == attention_mask.size(0) == max_length

        return {
            "input_ids": input_ids,
            "labels": labels,
            "attention_mask": attention_mask
        }

    def convert_seq2seq(self, source, target=None):
        inputs = self.tokenizer(
            source,
            add_special_tokens=True,
            max_length=self.max_source_tokens_count,
            padding="max_length",
            truncation=True,
            return_tensors="pt"
        )
        inputs = {k: v.squeeze(0) for k, v in inputs.items()}
        if target is not None:
            outputs = self.tokenizer(
                target,
                add_special_tokens=True,
                max_length=self.max_target_tokens_count,
                padding="max_length",
                truncation=True,
                return_tensors="pt"
            )
            labels = outputs["input_ids"].squeeze(0)
            labels[outputs["attention_mask"].squeeze(0) == 0] = -100
            inputs["labels"] = labels
        return inputs
