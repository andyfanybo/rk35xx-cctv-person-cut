# MPP session-list fix: ARM64 compile evidence

This recipe runs entirely on GitHub-hosted runners. The NAS does not fetch a
kernel tree, install a compiler, or execute a build.

The source revision is pinned to Rockchip's public commit
`4b382d7e3f500108b78e048281af56a4c507e3e1`. Its `rkvenc_show_session_info`
traverses the service session list using `session_link`, although
`mpp_dev_open` adds sessions using `service_link`. The patch corrects that
member. Newer upstream driver code also uses `service_link` here.

Sources:

- [Original traversal](https://github.com/rockchip-linux/kernel/blob/4b382d7e3f500108b78e048281af56a4c507e3e1/drivers/video/rockchip/mpp/mpp_rkvenc.c#L733-L748)
- [Session insertion](https://github.com/rockchip-linux/kernel/blob/4b382d7e3f500108b78e048281af56a4c507e3e1/drivers/video/rockchip/mpp/mpp_common.c#L1291-L1317)
- [Newer traversal](https://github.com/rockchip-linux/kernel/blob/develop-5.10/drivers/video/rockchip/mpp/mpp_rkvenc.c#L741-L756)

The workflow enables MPP proc support in the upstream
`rockchip_linux_defconfig`, cross-compiles the affected driver object for ARM64,
and checks its ELF architecture and the affected function symbol. Its artifact
contains the applied patch, build configuration, compiler output, source revision,
patched source and SHA-256 checksums. No deployment files, recording data,
credentials, model weights or local identifiers are included.

## Scope

The artifact establishes that the patch compiles against this pinned public
source. It does **not** establish binary compatibility with a vendor firmware,
repair a running kernel, or prove that every vendor modification is covered.

When the MPP driver is built into the kernel, this `.o` file cannot be loaded as
a replacement module. A deployable repair requires the vendor's matching full
kernel source, build configuration and boot packaging. This workflow does not
flash an image, reboot a device or access a NAS.

The patch changes Rockchip code, which retains its original GPL-2.0+/MIT terms.
The repository's existing licensing and attribution remain in effect for the
build recipe and documentation.
