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
from pathlib import Path


def test_authentication_source_never_formats_token_or_query_exception_into_logs():
    source = (Path(__file__).resolve().parents[4] / "api" / "apps" / "__init__.py").read_text(encoding="utf-8")

    assert "auth_token[:" not in source
    assert "{e_api_token}" not in source
    assert "{e_beta}" not in source
