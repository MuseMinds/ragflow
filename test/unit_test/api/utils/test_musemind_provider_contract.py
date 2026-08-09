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
from types import SimpleNamespace

import pytest

from api.utils.musemind_provider_contract import prepare_exact_document_upload


def test_prepare_exact_document_upload_makes_identity_opaque_and_create_only():
    upload = SimpleNamespace(filename="sensitive-original-name.PDF")
    document_id = "0123456789abcdef0123456789abcdef"

    create_only = prepare_exact_document_upload([upload], document_id)

    assert create_only is True
    assert upload.id == document_id
    assert upload.filename == f"{document_id}.pdf"


@pytest.mark.parametrize(
    ("uploads", "document_id", "message"),
    [
        ([SimpleNamespace(filename="document.txt")], "not-hex", "32 lowercase hexadecimal"),
        ([], "0123456789abcdef0123456789abcdef", "exactly one local file"),
        (
            [SimpleNamespace(filename="a.txt"), SimpleNamespace(filename="b.txt")],
            "0123456789abcdef0123456789abcdef",
            "exactly one local file",
        ),
    ],
)
def test_prepare_exact_document_upload_rejects_invalid_identity(uploads, document_id, message):
    with pytest.raises(ValueError, match=message):
        prepare_exact_document_upload(uploads, document_id)


def test_prepare_exact_document_upload_preserves_legacy_uploads():
    upload = SimpleNamespace(filename="original.txt")

    assert prepare_exact_document_upload([upload], "") is False
    assert upload.filename == "original.txt"
    assert not hasattr(upload, "id")
