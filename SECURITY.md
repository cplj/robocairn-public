# Security

Report vulnerabilities to hello@robocairn.com. Include the affected version,
reproduction steps, impact and a minimal synthetic example. Do not include private
observations or credentials. Avoid public disclosure of an unpatched issue.

Version 0.1.0 is experimental. The HTTP service has no authentication or encryption
and is intended for local, trusted use with its default loopback binding. Inputs
are bounded to 1 MiB; digest checks bind observations to a suite but do not prove
their origin. This tool does not assess or certify robot safety.
