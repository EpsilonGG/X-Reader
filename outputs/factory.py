"""Build the enabled output adapters from configuration.

Same shape as ``providers/factory.py``: configuration in, adapters out, and one
place to look when asking "what can this deployment deliver to?".

One deliberate behaviour: an enabled-but-unconfigured output still yields an
adapter. The alternative — returning nothing — would make "Telegram is switched
on but the secret is missing" look identical to "Telegram is switched off", and
the run would report success while delivering nothing. Instead the adapter is
built with empty credentials, and it fails *explicitly at delivery time*, leaving
every item pending so the next run picks them up once the credential is set
(task section 17).

The same reasoning applies to QQ, with one difference: QQ's ``api_base`` and
``group_id`` are not secrets, so they come from the config file, while only the
optional access token is read from the environment.

The credentials are read here and handed to the adapter as values; they are never
placed on a config object, so they cannot be serialised into a report or a log.

Adding an output means editing two files — the adapter module and this factory —
and nothing else. Telegram and QQ share no code beyond the delivery-result
contract and the two protocol-independent helpers in ``outputs/base.py``: they do
not import each other, and enabling one cannot change the other's behaviour.
"""
from __future__ import annotations

from config.schema import Config
from outputs.base import OutputAdapter
from outputs.qq import QQOutput
from outputs.qq import resolve_access_token
from outputs.telegram import TelegramOutput
from outputs.telegram import resolve_credentials


def build_outputs(config: Config, *, env: dict | None = None) -> list[OutputAdapter]:
    """Every output adapter this configuration enables. May be empty.

    Order is fixed (Telegram, then QQ) so a run's report is reproducible rather
    than dependent on dictionary iteration.
    """
    built: list[OutputAdapter] = []

    if config.telegram.enabled:
        bot_token, chat_id = resolve_credentials(
            config.telegram.bot_token_env,
            config.telegram.chat_id_env,
            env=env,
        )
        built.append(TelegramOutput(bot_token=bot_token, chat_id=chat_id))

    if config.qq.enabled:
        built.append(
            QQOutput(
                api_base=config.qq.api_base,
                group_id=config.qq.group_id,
                access_token=resolve_access_token(config.qq.access_token_env, env=env),
                timeout=config.qq.timeout,
            )
        )

    return built


def output_names(config: Config) -> list[str]:
    """Names of the enabled outputs, for reporting — without building anything."""
    names: list[str] = []
    if config.telegram.enabled:
        names.append(TelegramOutput.name)
    if config.qq.enabled:
        names.append(QQOutput.name)
    return names

