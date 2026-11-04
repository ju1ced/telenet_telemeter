# Changelog

All notable changes to this fork of Telenet Telemeter. Versions follow the
`version` in `custom_components/telenet_telemeter/manifest.json` and the GitHub
release tags that HACS installs from.

## [2.1.0] - 2026-11-04

First release of the [ju1ced/telenet_telemeter](https://github.com/ju1ced/telenet_telemeter)
fork. Requires Home Assistant 2024.1 or newer.

### Added
- BASE provider support next to Telenet: choose the provider when adding the
  integration; URLs and login flow follow the provider.
- Monetary data-bundle fallback for BASE mobile subscriptions.
- `reboot_internet` action: optional `config_entry_id` field, so Telenet and
  BASE accounts can be used side by side. Translated action and error messages
  (English, Dutch, French, Slovak).

### Changed
- Settings validation: a username, a password and at least one of internet or
  mobile are required, with one clear error message in the setup form. A
  failed submission can be corrected and resubmitted.
- HTTP failures (timeouts, connection errors, unexpected status codes) are
  raised instead of silently continuing with missing data.
- `reboot_internet` reports failures as Home Assistant errors instead of
  failing assertions, and always closes its session.
- Repository metadata, documentation and issue links point to this fork.

### Fixed
- Missing `ratelimit` requirement: a fresh installation no longer fails with
  `ModuleNotFoundError`. Unused requirements and imports are removed.
- `offPeak` `None` error in the internet sensor.
- Unloading or reloading an entry no longer leaves services or update
  listeners behind; removed the invalid `async_remove_entry` handlers.

### Security
- Debug logs no longer contain login state, nonces, state tokens, session
  cookies, response bodies or tokens in URLs. The account username is not
  logged when rebooting the modem.

### Removed
- YAML configuration. A `telenet_telemeter:` section in `configuration.yaml`
  only logs a warning; configure accounts via Settings > Devices & services.

### Upgrade notes
- Earlier versions created an empty "configuration.yaml" entry on start when a
  `telenet_telemeter:` YAML section was present. Such an entry has no
  credentials and now fails to set up: delete it under Settings > Devices &
  services > Telenet Telemeter and remove the YAML section.
- An entry with both internet and mobile switched off now fails to set up.
  Delete it and add the integration again with at least one of them enabled.

### Known issues
- The integration has no working options ("Configure") dialog yet; to change
  credentials or the tracked services, delete and re-add the entry. Tracked in
  [TM-07](https://github.com/ju1ced/telenet_telemeter/issues/8).

## [2.0.5]

Last version of the original integration by myTselection.

[2.1.0]: https://github.com/ju1ced/telenet_telemeter/releases/tag/v2.1.0
