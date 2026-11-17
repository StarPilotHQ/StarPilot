"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of jetlink and is licensed under the MIT License.
See the LICENSE file in the root directory for more details.

Which large models exist and how to get their bytes: the model index, which
names every big model's ONNX (index), and an ONNX by its oid (lfs).

The comma imports both, so this package stays stdlib only and imports nothing
itself. The server's side of it is JetlinkRegistry in Swift, held to these
modules by the registry conformance fixture (docs/conformance.md).
"""
