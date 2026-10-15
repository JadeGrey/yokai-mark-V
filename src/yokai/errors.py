"""Typed domain errors for Yokai."""

from __future__ import annotations

from typing import Optional, Sequence

from yokai.textutil import escape_md


class YokaiError(Exception):
    """Base exception for all domain errors in Yokai."""

    title: str = "That didn't work"

    def __init__(self, message: str, user_hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.user_hint = user_hint


class NotFoundError(YokaiError):
    """Raised when a track, query, or playlist yielded no results or was deleted."""

    title = "Nothing found"

    def __init__(
        self,
        message: str = "No matching track or video was found.",
        user_hint: Optional[str] = "Check the URL or try searching with artist and title.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class LoginRequiredError(YokaiError):
    """Raised when media is age-restricted, private, or members-only."""

    title = "Login required"

    def __init__(
        self,
        message: str = "This content requires a login (age-gated, private, or members-only).",
        user_hint: Optional[str] = "Try a public link or an official audio release.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class UnavailableError(YokaiError):
    """Raised when media is region-blocked, taken down, or unavailable."""

    title = "Track unavailable"

    def __init__(
        self,
        message: str = "This track is unavailable or region-restricted.",
        user_hint: Optional[str] = "Try an alternate upload or search query.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class BotCheckError(YokaiError):
    """Raised when YouTube issues a bot check, HTTP 403, or SABR format blockage."""

    title = "YouTube blocked the request"

    def __init__(
        self,
        message: str = "YouTube blocked the audio extraction request (bot detection/403).",
        user_hint: Optional[str] = "The bot owner can check /admin diag or update yt-dlp.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class ExtractionTimeoutError(YokaiError):
    """Raised when audio metadata or stream URL extraction timed out."""

    title = "Took too long"

    def __init__(
        self,
        message: str = "Extraction timed out while contacting the audio provider.",
        user_hint: Optional[str] = "Please try again in a few moments.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class UnsupportedError(YokaiError):
    """Raised when a URL domain, media format, or link type is unsupported."""

    title = "Not supported"

    def __init__(
        self,
        message: str = "This link or content type is not supported.",
        user_hint: Optional[str] = "Yokai accepts YouTube and Spotify track/album/playlist links.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class SpotifyResolutionError(YokaiError):
    """Raised when Spotify metadata resolution fails across all providers."""

    title = "Spotify lookup failed"

    def __init__(
        self,
        message: str = "Could not resolve metadata for this Spotify link.",
        user_hint: Optional[
            str
        ] = "Try pasting a YouTube link or searching with song title and artist.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class UnmatchedTrackError(YokaiError):
    """Raised when an imported Spotify track has no confident YouTube match."""

    title = "No match found"

    def __init__(self, title: str) -> None:
        super().__init__(
            f"Could not find a confident YouTube match for **{escape_md(title[:120])}**.",
            user_hint="Try searching with the song title and artist, or paste a YouTube link.",
        )


class VoiceError(YokaiError):
    """Base exception for voice channel connection or playback errors."""

    title = "Voice problem"


class VoicePermissionError(VoiceError):
    """Raised when the bot lacks necessary voice permissions (Connect or Speak)."""

    title = "Missing permission"

    def __init__(
        self,
        missing_permission: str,
        channel_name: str,
    ) -> None:
        msg = f"Missing permission '{missing_permission}' in voice channel #{channel_name}."
        hint = f"Please grant Yokai '{missing_permission}' permission in that channel's settings."
        super().__init__(msg, user_hint=hint)
        self.missing_permission = missing_permission
        self.channel_name = channel_name


class VoiceChannelError(VoiceError):
    """Raised when voice state rules are violated (e.g. caller not in VC)."""

    title = "Voice channel needed"

    def __init__(
        self,
        message: str = "You must be in a voice channel to use music commands.",
        user_hint: Optional[str] = "Join a voice channel and try again.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class DailyOnCooldownError(YokaiError):
    """Raised when /daily is claimed again before 24h have passed since the last claim."""

    title = "Already claimed"

    def __init__(self, retry_after_s: int) -> None:
        remaining = max(retry_after_s, 0)
        hours, rem = divmod(remaining, 3600)
        minutes = rem // 60
        if hours:
            wait_str = f"{hours}h {minutes}m"
        elif minutes:
            wait_str = f"{minutes}m"
        else:
            wait_str = "less than a minute"
        super().__init__(
            f"You already claimed your daily reward. Try again in {wait_str}.",
            user_hint="Daily rewards reset 24 hours after your last claim.",
        )
        self.retry_after_s = retry_after_s


class InsufficientFundsError(YokaiError):
    """Raised when a payment, bet or debit would exceed the payer's balance."""

    title = "Not enough Renown"

    def __init__(self, balance: int, amount: int, action: str = "send") -> None:
        super().__init__(
            f"You have **{balance:,}** Renown, which isn't enough to {action} **{amount:,}**.",
            user_hint="Earn more with /daily or by using Yokai in voice.",
        )
        self.balance = balance
        self.amount = amount


class InvalidPaymentError(YokaiError):
    """Raised when a /pay request targets an invalid recipient or amount."""

    title = "Invalid payment"

    def __init__(self, message: str) -> None:
        super().__init__(message, user_hint="Pick a positive whole number and a real member.")


class BetOutOfRangeError(YokaiError):
    """Raised when a stake is outside the configured minimum and maximum."""

    title = "Bet out of range"

    def __init__(self, minimum: int, maximum: int) -> None:
        super().__init__(
            f"Bets must be a whole number from **{minimum:,}** to **{maximum:,}** Renown.",
            user_hint="Pick an amount inside that range and try again.",
        )
        self.minimum = minimum
        self.maximum = maximum


class AlreadyInGameError(YokaiError):
    """Raised when a member starts a game while another of theirs is still running."""

    title = "Already in a game"

    def __init__(self) -> None:
        super().__init__(
            "You already have a game in progress.",
            user_hint="Finish your current game first.",
        )


class BetNotOpenError(YokaiError):
    """Raised when settling or refunding a bet that is not open (settled twice, or unknown)."""

    title = "Bet already settled"

    def __init__(self, bet_id: int) -> None:
        super().__init__(f"Bet #{bet_id} is not open.")
        self.bet_id = bet_id


class InvalidGameChoiceError(YokaiError):
    """Raised when a game option (such as a roulette bet type or pick) is invalid."""

    title = "Invalid choice"

    def __init__(self, message: str) -> None:
        super().__init__(message, user_hint="Check the command options and try again.")


class TableBusyError(YokaiError):
    """Raised when a table is requested in a channel that already has a live one."""

    title = "Table in use"

    def __init__(self) -> None:
        super().__init__(
            "A table is already open in this channel.",
            user_hint="Wait for it to finish, or ask the host to cancel it if it has not started.",
        )


class ChannelNotConfiguredError(YokaiError):
    """Raised when Play is pressed in a channel that is not a table channel."""

    title = "Not a table channel"

    def __init__(self) -> None:
        super().__init__(
            "This channel is not set up for a multiplayer game.",
            user_hint="An admin can set one up with /admin set_multiplayer.",
        )


class LobbyClosedError(YokaiError):
    """Raised when a table action is not possible in the table's current state."""

    title = "Table closed"

    def __init__(
        self,
        message: str = "This table is no longer open.",
        user_hint: Optional[str] = "Press Play in the table channel to start a new one.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class NotAtTableError(YokaiError):
    """Raised when someone who is not seated tries to act as a player."""

    title = "Not at this table"

    def __init__(self) -> None:
        super().__init__(
            "You are not seated at this table.",
            user_hint="Only players at the table can do that.",
        )


class NotHostError(YokaiError):
    """Raised when a host-only table action is attempted by someone else."""

    title = "Host only"

    def __init__(self) -> None:
        super().__init__(
            "Only the host or an admin can do that.",
            user_hint="Ask the host of this table.",
        )


class TablePermissionError(YokaiError):
    """Raised when the bot lacks permissions in a channel chosen as a table channel."""

    title = "Missing permission"

    def __init__(self, missing: Sequence[str], channel_name: str) -> None:
        listed = ", ".join(missing)
        super().__init__(
            f"I am missing these permissions in #{channel_name}: {listed}.",
            user_hint="Grant them to Yokai in that channel's settings and run the command again.",
        )
        self.missing = list(missing)
        self.channel_name = channel_name


class NotYourTurnError(YokaiError):
    """Raised when a player acts at a turn-based table out of turn."""

    title = "Not your turn"

    def __init__(self) -> None:
        super().__init__(
            "It isn't your turn yet.",
            user_hint="Wait until the table is waiting on you.",
        )


class LotteryClosedError(YokaiError):
    """Raised when tickets are bought while sales are closed."""

    title = "Tickets closed"

    def __init__(
        self,
        message: str = "The draw is happening right now, so ticket sales are closed.",
        user_hint: Optional[str] = "Tickets for the next draw go on sale in a moment.",
    ) -> None:
        super().__init__(message, user_hint=user_hint)


class TicketLimitError(YokaiError):
    """Raised when a purchase would take a member past the per-draw ticket cap."""

    title = "Ticket limit"

    def __init__(self, held: int, maximum: int) -> None:
        super().__init__(
            f"You can hold at most **{maximum}** tickets per draw, and you already have "
            f"**{held}**.",
            user_hint="Wait for the next draw to buy more.",
        )
        self.held = held
        self.maximum = maximum


class InvalidPokerActionError(YokaiError):
    """Raised when a poker move is not allowed right now (bad raise size, nothing to call...)."""

    title = "Can't do that"


class InvalidCombatActionError(YokaiError):
    """Raised when a fight action is not allowed right now (cooldown, bad target, fight over)."""

    title = "Can't do that"


class NoAttemptsError(YokaiError):
    """Raised when a member does not have enough daily attempts for a mission."""

    title = "Out of attempts"

    def __init__(self, remaining: int, cost: int, resets_ts: Optional[int] = None) -> None:
        hint = "Attempts refresh every day."
        if resets_ts is not None:
            hint = f"Your attempts refresh <t:{resets_ts}:R>."
        super().__init__(
            f"That mission costs {cost} attempt(s) and you have {remaining} left today.",
            user_hint=hint,
        )
        self.remaining = remaining
        self.cost = cost


class MissionNotAvailableError(YokaiError):
    """Raised when a mission is not on today's guild board."""

    title = "Not on the board"

    def __init__(self) -> None:
        super().__init__(
            "That mission isn't on today's board.",
            user_hint="Run /guild board to see today's missions.",
        )


class MissionRankError(YokaiError):
    """Raised when a mission is above the member's rank."""

    title = "Rank too low"

    def __init__(self, mission_rank: str, your_rank: str) -> None:
        super().__init__(
            f"That mission needs rank {mission_rank} and you are rank {your_rank}.",
            user_hint="Missions at your rank or below are open to you.",
        )


class AlreadyFightingError(YokaiError):
    """Raised when a member who is already in a guild fight tries to start another."""

    title = "Already in a fight"

    def __init__(self) -> None:
        super().__init__(
            "You're already in a guild fight.",
            user_hint="Finish it in its thread first.",
        )


class NotInFightError(YokaiError):
    """Raised when someone who is not in a guild fight presses its buttons."""

    title = "Not your fight"

    def __init__(self) -> None:
        super().__init__(
            "Only the adventurers in this fight can act in it.",
            user_hint="You can start your own with /guild mission.",
        )


class ThreadUnavailableError(YokaiError):
    """Raised when a fight thread cannot be opened where the command was used."""

    title = "Can't open a thread"

    def __init__(self) -> None:
        super().__init__(
            "I couldn't open a fight thread here.",
            user_hint="Run the command in a normal text channel where I can create threads.",
        )


class GearNotFoundError(YokaiError):
    """Raised when an item is not one the member owns."""

    title = "No such item"

    def __init__(self) -> None:
        super().__init__(
            "You don't have that item.",
            user_hint="Run /gear inventory to see what you own.",
        )


class GearLevelTooLowError(YokaiError):
    """Raised when a member tries to wear gear above their level."""

    title = "Level too low"

    def __init__(self, item_name: str, required: int, level: int) -> None:
        super().__init__(
            f"{item_name} needs level {required} and you are level {level}.",
            user_hint="You can keep it, trade it later, or sell it.",
        )


class ItemEquippedError(YokaiError):
    """Raised when an equipped item is sold or traded."""

    title = "Item is equipped"

    def __init__(self) -> None:
        super().__init__(
            "That item is equipped.",
            user_hint="Run /gear unequip first.",
        )


class GearLockedError(YokaiError):
    """Raised when gear is changed during a fight."""

    title = "Gear is locked"

    def __init__(self) -> None:
        super().__init__(
            "You can't change your gear while you're in a fight.",
            user_hint="Finish the fight in its thread first.",
        )


class InventoryFullError(YokaiError):
    """Raised when an item cannot be added because the inventory is full."""

    title = "Inventory full"

    def __init__(self, cap: int) -> None:
        super().__init__(
            f"You already hold {cap} items.",
            user_hint="Sell or trade something with /gear sell first.",
        )


class ShopLimitError(YokaiError):
    """Raised when a member has used their daily shop purchases."""

    title = "Shop limit reached"

    def __init__(self, cap: int) -> None:
        super().__init__(
            f"You can buy {cap} items from the guild shop per day, and you've bought them.",
            user_hint="The shop restocks and your limit resets at the next guild day.",
        )


class NotInStockError(YokaiError):
    """Raised when an item is not in today's guild shop."""

    title = "Not in stock"

    def __init__(self) -> None:
        super().__init__(
            "That item isn't in today's guild shop.",
            user_hint="Run /gear shop to see today's stock.",
        )


class NothingToRepairError(YokaiError):
    """Raised when there is nothing damaged to repair."""

    title = "Nothing to repair"

    def __init__(self) -> None:
        super().__init__(
            "None of that gear is damaged.",
            user_hint="Worn gear shows its durability in /gear inventory.",
        )


class PromotionNotReadyError(YokaiError):
    """Raised when a member is below the level a promotion needs."""

    title = "Not ready to promote"

    def __init__(self, next_rank: str, needed: int, level: int) -> None:
        super().__init__(
            f"Rank {next_rank} opens at level {needed} and you are level {level}.",
            user_hint="Keep taking missions to level up.",
        )


class PromotionCooldownError(YokaiError):
    """Raised when a member tries another promotion fight too soon after losing one."""

    title = "Still recovering"

    def __init__(self, until_ts: int) -> None:
        super().__init__(
            "You lost your last promotion fight and need to recover first.",
            user_hint=f"You can try again <t:{until_ts}:R>.",
        )
        self.until_ts = until_ts


class PromotionStaleError(YokaiError):
    """Raised when a promotion no longer matches the member's rank."""

    title = "Your rank changed"

    def __init__(self) -> None:
        super().__init__(
            "That promotion doesn't match your current rank.",
            user_hint="Run /guild rank again.",
        )


class TopRankError(YokaiError):
    """Raised when a member who holds the highest rank asks to promote."""

    title = "Top rank"

    def __init__(self) -> None:
        super().__init__(
            "You already hold the highest rank in the guild.",
            user_hint="Harder missions arrive with future updates.",
        )


class NoCharacterError(YokaiError):
    """Raised when someone looks up a guild character that does not exist."""

    title = "No adventurer yet"

    def __init__(self, own: bool = True) -> None:
        if own:
            super().__init__(
                "You haven't joined the adventurers' guild yet.",
                user_hint="Run /guild join to create your character.",
            )
        else:
            super().__init__(
                "That member hasn't joined the adventurers' guild yet.",
                user_hint="They can run /guild join to create a character.",
            )


class PartyFullError(YokaiError):
    """Raised when someone tries to join a party that already has the most members."""

    title = "Party is full"

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"This party already has {limit} members.",
            user_hint="Wait for the next lobby or ask the leader to open another.",
        )


class AlreadyInPartyError(YokaiError):
    """Raised when a member who is already in a lobby tries to join it again."""

    title = "Already in the party"

    def __init__(self) -> None:
        super().__init__("You're already in this party.", user_hint="Wait for the leader to start.")


class NotInPartyError(YokaiError):
    """Raised when someone who is not in a lobby tries to leave it."""

    title = "Not in the party"

    def __init__(self) -> None:
        super().__init__("You're not in this party.", user_hint="Press Join to take a seat.")


class NotPartyLeaderError(YokaiError):
    """Raised when someone other than the leader tries to start or cancel a lobby."""

    title = "Leader only"

    def __init__(self) -> None:
        super().__init__(
            "Only the party leader can do that.",
            user_hint="Ask the leader, or open your own lobby with /guild mission.",
        )


class PartyTooSmallError(YokaiError):
    """Raised when a lobby is started with fewer members than the mission needs."""

    title = "Not enough members"

    def __init__(self, needed: int, have: int) -> None:
        super().__init__(
            f"This mission needs a party of at least {needed} and you have {have}.",
            user_hint="Wait for others to join, then press Start.",
        )
