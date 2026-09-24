from torch import Tensor

class BaseModelOutputWithPoolingAndCrossAttentions:
    last_hidden_state: Tensor

class BertModel:
    @classmethod
    def from_pretrained(
        cls, path: str, *, local_files_only: bool, use_safetensors: bool, attn_implementation: str
    ) -> BertModel: ...
    def eval(self) -> BertModel: ...
    def __call__(
        self, *, input_ids: Tensor, attention_mask: Tensor
    ) -> BaseModelOutputWithPoolingAndCrossAttentions: ...
