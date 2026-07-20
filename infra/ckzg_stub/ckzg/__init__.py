"""Pure-Python stub for the `ckzg` native package (Windows SAC workaround).

Windows Smart App Control blocks ckzg's unsigned C extension DLL, which
breaks `import eth_account` entirely (its typed_transactions module imports
ckzg at module load). ckzg is only actually CALLED for EIP-4844 blob
transactions (type 3), which baryon never creates — legacy and EIP-1559
transactions and EIP-712 signing don't touch KZG commitments.

Same treatment as infra/bitarray_stub: copy this package over
.venv/Lib/site-packages/ckzg* after any venv rebuild. `pip install ckzg`
would restore the blocked DLL.
"""

_MSG = (
    "ckzg is stubbed out on this machine (Windows Smart App Control blocks the "
    "native DLL). Blob (EIP-4844) transactions are not supported — and nothing "
    "in baryon should be sending them."
)


def load_trusted_setup(*args, **kwargs):  # noqa: ANN002, ANN003
    raise NotImplementedError(_MSG)


def blob_to_kzg_commitment(*args, **kwargs):  # noqa: ANN002, ANN003
    raise NotImplementedError(_MSG)


def compute_blob_kzg_proof(*args, **kwargs):  # noqa: ANN002, ANN003
    raise NotImplementedError(_MSG)
