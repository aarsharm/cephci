from ceph.nvmegw_cli.execute import ExecuteCommandMixin


class Host(ExecuteCommandMixin):
    def __init__(self, node, port) -> None:
        super().__init__(node, port)
        self.name = "host"

    def add(self, **kwargs):
        return self.run_nvme_cli("add", **kwargs)

    def delete(self, **kwargs):
        return self.run_nvme_cli("del", **kwargs)

    def list(self, **kwargs):
        return self.run_nvme_cli("list", **kwargs)
