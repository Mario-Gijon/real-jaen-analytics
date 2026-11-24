"""Player photos: the photos-v1 transfer contract and the photo store under /state/player-photos/.

Deliberately outside persistencia/ and outside every file listed by persistencia.carga.archivos_de_codigo(): nothing
here takes part in the coordinator's rule fingerprint, and nothing here reads or writes the sporting SQLite
generations. Standard library only. Not wired to HTTP or to the update control yet. See docs/contrato-fotos-v1.md.
"""
