#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import re
from pathlib import Path


def prepare_exact_document_upload(file_objs, requested_document_id: str) -> bool:
    """Apply the MuseMind single-file create-only identity contract in place."""
    if not requested_document_id:
        return False
    if re.fullmatch(r"[0-9a-f]{32}", requested_document_id) is None:
        raise ValueError("`document_id` must be 32 lowercase hexadecimal characters")
    if len(file_objs) != 1:
        raise ValueError("A caller-supplied `document_id` requires exactly one local file")

    suffix = Path(file_objs[0].filename or "").suffix.lower()
    file_objs[0].id = requested_document_id
    file_objs[0].filename = f"{requested_document_id}{suffix}"
    return True
