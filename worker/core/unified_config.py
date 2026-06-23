from dataclasses import dataclass, field

@dataclass
class UnifiedConfig:
    protocol: str
    server: str
    port: int
    auth: dict= field(
        default_factory=dict
    )
    transport: dict= field(
        default_factory=dict
    )
    tls: dict= field(
        default_factory=dict
    )
    metadata: dict= field(
        default_factory=dict
    )

    raw_url: str=""