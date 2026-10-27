# Beacon G6 write forms

Verified 2026-09-28 against UI code version 2501.20.03.A17. These are protocol notes
derived from the router's web UI; Nokia's JavaScript bundles are not vendored.

## Sources

- `819.bb4077ef08691a21.js`: `saveDnsIpv4ConfigData`, `deleteData`,
  `saveDnsDomainConfigData`, static DHCP save handler.
- `25.9712371620b285ec.js`: `savePortForwardingInfo`, `deletePortForwarding`,
  WAN list and port-range normalization.
- `787.18cda76300f11c1e.js`: backup export and `config.cfg` download.
- `main.6ee07b113ae3f8bf.js`: API method transport and endpoint mapping.

The router serves these under `/web_whw/`. Temporary research copies were saved outside
the repository. The firmware may change these contracts; other models remain unverified.

## Local DNS

- Read: `GET dns_status_web_app.cgi`, with `proxy_cfg.Enable`, `dns_cfg[]`
  (`_oid`, `domainName`, `domainAddr`) and `domain_cfg[]` for aliases.
- Add: `POST dns_web_app.cgi?add`, body `domainName=<hostname>&domainAddr=<IPv4>`.
- Delete: `POST dns_web_app.cgi?act=del&oid=<row._oid>`, no application body.
- Update: delete then add, since the UI has no record edit operation.
- Alias add (not exposed): `POST dns_web_app.cgi?mod`,
  `origindomain=<name>&newdomain=<name>`; rows use `SdomainName` / `DdomainName`.
- Alias delete (not exposed): `POST dns_web_app.cgi?act=dell&oidd=<id>`.
- Proxy toggle (not exposed): `POST dns_web_app.cgi?ena`, `prox_ena=on` or empty;
  the UI warns this reboots the router.

## Port forwarding

Read: `GET nat_glb_status_web_app.cgi?v=vhost`. Rules live under
`port_mappings[] -> ipConns[] / pppConns[] -> portmaps[]`; the same response has
`port_triggerings` and `lan_ifip` for validation. IDs are not globally unique:
UPnP entries can repeat a static rule's `_oid`.

Add: `POST nat_glb_web_app.cgi?v=add_vhost`, with the custom-application form:

```text
appname=0
nat_value=0
etPortStart=<external start>
etPortEnd=<external end>
inPortStart=<internal start>
inPortEnd=<internal end>
servername=0
serverIp=<LAN IPv4>
proto=<TCP|UDP|TCPorUDP>
wanif=<ip|ppp>,<group.did>,<group._oid>,<connection._oid>
en_map=on
```

`servername=0` chooses a manually supplied server IP rather than a device MAC. Form
values use URI-component encoding, including the commas in `wanif`. `appname=0` selects
the custom form, not a user-visible rule name. `nat_value=0` disables predefined bundles.

Delete: `POST nat_glb_web_app.cgi?v=del_vhost&t=<ip|ppp>&id=<did>,<group oid>,<connection oid>,<rule oid>`.
No application body. Static rules have no `X_ASB_COM_Cfg_Source` in the verified state;
UPnP uses `UPnP`. The tool excludes all non-static sources from deletion.

The UI treats a zero port-range bound as equal to the opposite bound. The MCP only creates
explicit ranges in 1–65535, with equal external/internal lengths.

## Backup

`GET usb_web_app.cgi?export` returns opaque configuration bytes. The client already
implements authenticated raw-byte reads. Export files are not assumed to be JSON and
their contents are never printed. Nonempty download and checksum were verified; restore
was not tested because it reboots and replaces router configuration.

## Write result handling

CSRF and RSA/AES encryption are supplied by `NokiaRouter.post`. Auth errors, HTML replies,
timeouts and connection failures must not cause POST replay. Tools send one POST and poll
the corresponding status endpoint for the desired state. A nonzero `result` (or `ret`
when no `result` is present) is treated as rejection, but read-back remains authoritative.
Missing/invalid status data must not be interpreted as an empty rule list.

## Hardware verification

On 2026-09-28, after exporting a configuration backup:

1. Created a temporary hostname pointing to a test LAN host and queried the router's DNS server.
2. Replaced its address with another test address; repeated set returned `unchanged`.
3. Deleted the temporary hostname; repeated delete returned `unchanged`.
4. Verified TCP port 49173 on the test host was closed; created an external TCP 49173
   forward to that same port, verified read-back, and confirmed repeated add was a no-op.
5. Deleted the temporary forward and confirmed repeated delete was a no-op.
6. Compared all original DNS state and normalized static forward state to the baseline:
   unchanged. UPnP state was not included in equality checks because it changes independently.

No reboot, DNS proxy toggle, restore, or existing-rule change was performed. External WAN
traffic was not tested. Remaining hardware coverage: UDP/both-protocol ranges, multiple WANs,
and rejection/recovery behavior (covered offline).
