# Security policy

## Supported versions

Only the **latest release** of the integration receives security fixes.
Please update to it before reporting, and check whether the problem is still
there.

| Version        | Supported |
| -------------- | --------- |
| Latest release | Yes       |
| Older releases | No        |

## Reporting a vulnerability

Please report vulnerabilities **privately**, through GitHub's private
vulnerability reporting: open the
[Security tab](https://github.com/candrea77/ha-besmart/security) of this
repository and click **Report a vulnerability**
([direct link](https://github.com/candrea77/ha-besmart/security/advisories/new)).

**Do not open a public issue, discussion or pull request** for a security
problem.

A useful report includes:

- the integration version and the Home Assistant version;
- what the problem is and what an attacker could do with it;
- the steps to reproduce it.

## What not to include

Never put the following in a report, in attachments or in screenshots:

- passwords, usernames or e-mail addresses used for the BeSMART cloud;
- API tokens;
- WiFi box ids, thermostat ids and boiler ids, and your BeSMART user id;
- logs that are not masked. If a log is needed, remove these values first.

If something is needed to reproduce the problem, we will ask for it.

## Scope

This policy covers the **code of this integration** (the files in this
repository).

Problems in the **BeSMART cloud service**, in the **BeSMART app** or in the
devices themselves are not in scope: please report them to Riello.

## Response times

This project is maintained by one person in their spare time. Reports are
handled on a **best-effort** basis: there is no guaranteed response or fix
time, but every private report will be read.
