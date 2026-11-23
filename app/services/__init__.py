"""Servicios de la aplicación web.

Equivalencias con la estructura sugerida:
    vault_service.py   -> envuelve signer.vault.Vault (sin bloquear el event loop)
    signer_service.py  -> SignerRuntime: arranca/detiene signer.service.SignerService
    approval_service   -> no se crea: se reutiliza signer.approvals.ApprovalManager,
                          una sola instancia compartida entre el firmante y la API
"""
