"""Which channels a backup lives in, and the rows an account reads them through.

A Telegram channel holds a finite number of messages, so a job whose source is larger than
one channel can take spreads its files over several: the channel it was created with, and
the extra channels in `sync_job_channels`. Every file entry records the channel its parts
were sent to, and that column, not the channel of the job, is what everything that reads a
file asks. The runner decides it once per file, when the file is claimed for upload, by
picking the channel holding the fewest files.

The consequence for everything that reads a channel is the **group**: the channels that
share a sync job with it, followed transitively. A backup spread over three channels is
one backup, so opening any of them in the explorer, restoring a folder from it or pointing
a download job at it reads all three. A channel no job spreads is a group of one, which is
exactly how the application behaved before.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, or_, select

from .models import Channel, FileEntry, SyncJob, SyncJobChannel, TelegramAccount, utcnow
from .telegram.manager import TelegramError, manager


class ChannelError(ValueError):
    """Error meant to be shown directly to the user."""


def writer_jobs(channel_ids):
    """A select of the ids of the sync jobs writing to any of these channels.

    `channel_ids` is a list or a select of ids. A job writes to its own channel and to its
    extra ones alike, and every question of the form "is a job using this channel" has to
    count both, or a channel used only as an extra would look free.
    """
    return select(SyncJob.id).where(
        or_(
            SyncJob.channel_id.in_(channel_ids),
            SyncJob.id.in_(
                select(SyncJobChannel.job_id).where(SyncJobChannel.channel_id.in_(channel_ids))
            ),
        )
    )


async def job_channel_ids(session, job: SyncJob) -> list[int]:
    """The channels a job uploads into, its own first and the extra ones in the order added."""
    result = await session.execute(
        select(SyncJobChannel.channel_id)
        .where(SyncJobChannel.job_id == job.id)
        .order_by(SyncJobChannel.id)
    )
    extras = [channel_id for (channel_id,) in result if channel_id != job.channel_id]
    return [job.channel_id, *extras]


async def channel_group(session, channel_id: int) -> list[int]:
    """This channel and every channel a sync job spreads it with, the one asked for first.

    Followed transitively, because two jobs can chain: one spread over A and B, another
    over B and C. Reading A without C would give back part of the second job and nothing
    saying so. The loop ends because every pass either finds a channel not yet seen or
    stops, and an installation has a handful of jobs.
    """
    group = [channel_id]
    seen = {channel_id}
    frontier = [channel_id]
    while frontier:
        jobs = list((await session.execute(writer_jobs(frontier))).scalars())
        frontier = []
        if not jobs:
            break
        primaries = select(SyncJob.channel_id).where(SyncJob.id.in_(jobs))
        extras = select(SyncJobChannel.channel_id).where(SyncJobChannel.job_id.in_(jobs))
        for (found,) in await session.execute(primaries.union(extras)):
            if found not in seen:
                seen.add(found)
                group.append(found)
                frontier.append(found)
    return group


async def channel_fill(session, channel_ids: Iterable[int]) -> dict[int, int]:
    """How many files each of these channels holds, whichever job put them there.

    A pending entry is not counted: it has no message yet, and the channel written on it
    is a placeholder until the runner places it. Every other state has, or had until a
    moment ago, messages in that channel.
    """
    ids = list(channel_ids)
    result = await session.execute(
        select(FileEntry.channel_id, func.count(FileEntry.id))
        .where(FileEntry.channel_id.in_(ids), FileEntry.state != "pending")
        .group_by(FileEntry.channel_id)
    )
    counts = dict(result.all())
    return {channel_id: counts.get(channel_id, 0) for channel_id in ids}


async def account_channel(session, account_id: int, tg_id: int) -> Channel:
    """The row this account holds for one Telegram channel, fetched from its dialogs if new.

    Channel rows are per account, because the access_hash is issued per user: reading a
    channel through an account means reading it through that account's own row, never
    through the row of whoever discovered it. A row that is already here is taken as it
    is, since it was written from the dialogs of this account and therefore proves it saw
    the channel. When there is none, or it has no access_hash to build a peer from, the
    dialogs are read, and failing to find the channel there is the answer to give: this
    account is not in it.
    """
    channel = await session.scalar(
        select(Channel).where(Channel.account_id == account_id, Channel.tg_id == tg_id)
    )
    if channel is not None and (channel.kind == "group" or channel.access_hash is not None):
        return channel

    account = await session.get(TelegramAccount, account_id)
    label = account.label if account else str(account_id)
    try:
        fetched = await manager.list_channels(account_id)
    except (TelegramError, OSError, ValueError) as exc:
        raise ChannelError(
            f"The channels of the account {label} could not be read: {exc}"
        ) from exc

    live = next((item for item in fetched if item["tg_id"] == tg_id), None)
    if live is None:
        known = await session.scalar(select(Channel.title).where(Channel.tg_id == tg_id))
        raise ChannelError(
            f"The account {label} is not in the channel {known or tg_id}. Join it with "
            "that account, then try again."
        )

    if channel is None:
        # A row with no account behind it is one a bot set created by naming the channel,
        # and it is adopted rather than duplicated: one Telegram channel is one row, so a
        # job moved from a bot set onto an account keeps pointing at the same index.
        channel = await session.scalar(
            select(Channel).where(Channel.account_id.is_(None), Channel.tg_id == tg_id)
        )
    if channel is None:
        channel = Channel(account_id=account_id, tg_id=tg_id)
        session.add(channel)
    channel.account_id = account_id
    channel.access_hash = live["access_hash"]
    channel.title = live["title"]
    channel.username = live["username"]
    channel.is_private = live["is_private"]
    channel.kind = live["kind"]
    channel.participants = live["participants"]
    channel.last_seen_at = utcnow()
    await session.flush()
    return channel


async def account_rows(session, account_id: int, channel_ids: Iterable[int]) -> dict[int, Channel]:
    """For every channel id, the row this account can build a peer from.

    The ids come from file entries, which name the row of whoever uploaded them: a bot set
    job writes into an ownerless row, a download job may read through an account other
    than the one that sent the files. The messages belong to the channel either way, so
    what has to change is only the row, matched by Telegram id.
    """
    ids = list(dict.fromkeys(channel_ids))
    rows = {
        row.id: row
        for row in (await session.execute(select(Channel).where(Channel.id.in_(ids)))).scalars()
    }
    resolved: dict[int, Channel] = {}
    for channel_id in ids:
        row = rows.get(channel_id)
        if row is None:
            raise ChannelError("A channel this backup was spread over is no longer known here")
        if row.account_id == account_id and (row.kind == "group" or row.access_hash is not None):
            resolved[channel_id] = row
        else:
            resolved[channel_id] = await account_channel(session, account_id, row.tg_id)
    return resolved
