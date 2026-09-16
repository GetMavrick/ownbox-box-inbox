"""Configuration: environment variables + config/aios.config.yaml.

`yaml` is imported lazily inside get_config() so the state/queue spine can be
imported (and smoke-tested) with the standard library alone.
"""
import functools
import os
from pathlib import Path

# Resolve ROOT from THIS file's location, NOT the cwd. A process launched from any
# working directory (a subprocess, a hand-run script, a timer without WorkingDirectory)
# must still find the box's .env — otherwise load_dotenv() with no path walks up from the
# CWD, misses /opt/aios/.env, and EVERY secret silently reads empty. That was the source of
# the intermittent `claude_code FAILED: CLAUDE_CODE_OAUTH_TOKEN not set` pages.
ROOT = Path(__file__).resolve().parent.parent

# HERMETIC-TEST ESCAPE HATCH: the scrub headers in tests/test_*.py pop AIRTABLE_*/
# SLACK_*/etc from os.environ BEFORE importing this module — but load_dotenv() then
# re-injects those exact keys from the box's .env FILE, silently un-scrubbing them
# (dotenv only skips keys still PRESENT in the environment). That leak made
# test_airtable_sync/test_carousel fail on any box whose .env renames Airtable fields
# (the #205 revert) and DM'd the real operator from test runs (#199's bug class).
# AIOS_HERMETIC_TEST=1 skips the .env load entirely: tests see ONLY what they set.
if not os.environ.get("AIOS_HERMETIC_TEST"):
    try:  # dotenv is convenient but not required — env still works without it
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")   # EXPLICIT path — cwd-independent (never bare load_dotenv())
    except Exception:  # pragma: no cover
        pass
CONFIG_PATH = os.environ.get("AIOS_CONFIG", str(ROOT / "config" / "aios.config.yaml"))


class Settings:
    """Process-level settings sourced from the environment."""
    anthropic_api_key      = os.environ.get("ANTHROPIC_API_KEY", "")
    # Long-lived `claude setup-token` token (brain.backend=claude_code, owner box
    # only). The CLI reads this env var itself; settings carries it for probes.
    claude_code_oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    dispatch_bearer_token  = os.environ.get("DISPATCH_BEARER_TOKEN", "")
    # NARROW KEY FOR THE DEPLOY DOOR. Optional: unset, /deploy falls back to the bearer above.
    # Set, it is the only key to /deploy. It exists because the bearer is ALSO the dashboard
    # login (see dash_token below) and the unsubscribe signing seed (core/compliance), and the
    # deploy key has to be typed into a remote session on a phone.
    deploy_token           = os.environ.get("DEPLOY_TOKEN", "")
    # WHO THE AUTOPILOT'S COMMITS BELONG TO. Owner, 2026-09-07: "I absolutely need this to be
    # committing and merging under my name." The PR is already opened by the token's owner —
    # that is him — but the COMMIT was authored by a hard-coded "AIOS autopilot", so GitHub
    # attributed it to nobody and his contribution graph never saw it. Set these to a name and
    # an email REGISTERED TO THE GITHUB ACCOUNT (the users.noreply address works) and the
    # commits are his. Left unset, a clone keeps committing as the machine, which is correct:
    # a buyer's box must never author commits as us.
    autopilot_git_name     = os.environ.get("AUTOPILOT_GIT_NAME", "AIOS autopilot")
    autopilot_git_email    = os.environ.get("AUTOPILOT_GIT_EMAIL", "autopilot@users.noreply.github.com")
    # The DASHBOARD password, separate from the command credential above. Defaults to the
    # bearer so an existing box changes nothing — but once the person logging in is a buyer
    # rather than the operator, those two must be able to differ. The bearer drives the whole
    # box through /dispatch; the dash only shows it. Under one secret, showing someone the
    # dashboard means handing them the box, by typing it into a browser form.
    dash_token             = os.environ.get("DASH_TOKEN", "") or dispatch_bearer_token
    # Signs unsubscribe links — separate from the bearer so token rotation can
    # never break opt-out links in already-sent email (CAN-SPAM ≥30-day rule).
    unsub_signing_key      = os.environ.get("UNSUB_SIGNING_KEY", "")
    slack_bot_token        = os.environ.get("SLACK_BOT_TOKEN", "")
    # Socket-Mode credential for the clone bot (xapp-…, scope connections:write).
    slack_app_token        = os.environ.get("SLACK_APP_TOKEN", "")
    # Channel allowlist → intent map for the clone bot, e.g. "C0A1:reel,C0B2:brain".
    # Channels NOT in this map are ignored by construction (integration rule 1).
    slack_channel_intents  = os.environ.get("SLACK_CHANNEL_INTENTS", "")
    # DMs to the bot route by first word (reel/gtm commands → their department,
    # else brain). Set SLACK_DM_INTENT to pin every DM to one lane instead.
    slack_dm_intent        = os.environ.get("SLACK_DM_INTENT", "")
    operator_slack_user_id = os.environ.get("OPERATOR_SLACK_USER_ID", "")
    # Ownbox's provisioner, as user@host, which the operator's own box reads over ssh to page a person when
    # an order needs one (core/watchdog.py). Unset on every customer box, which keeps the probe inert.
    ownbox_orders_ssh      = os.environ.get("OWNBOX_ORDERS_SSH", "")
    # The reel channel — morning-drop posts land here, and dashboard-approved
    # rows with no recorded origin fall back to it for their ready notice.
    reel_slack_channel_id  = os.environ.get("REEL_SLACK_CHANNEL_ID", "")
    db_path                = os.environ.get("AIOS_DB_PATH", str(ROOT / "aios.db"))
    healthcheck_url        = os.environ.get("HEALTHCHECK_URL", "")
    physical_address       = os.environ.get("AIOS_PHYSICAL_ADDRESS", "")
    # Object store (S3-compatible: Cloudflare R2 / Backblaze B2 / Supabase / S3).
    # Shared with Litestream (#42); the same bucket can hold the DB replica and
    # the reel videos. Set all five to serve finished videos from a CDN instead
    # of the box (fixes choppy remote playback). PUBLIC_URL is the CDN base the
    # <video> tags use (e.g. https://media.nlvl.co or https://pub-xxx.r2.dev).
    object_store_bucket    = os.environ.get("OBJECT_STORE_BUCKET", "")
    object_store_endpoint  = os.environ.get("OBJECT_STORE_ENDPOINT", "")
    object_store_key       = os.environ.get("OBJECT_STORE_KEY", "")
    object_store_secret    = os.environ.get("OBJECT_STORE_SECRET", "")
    object_store_public_url = os.environ.get("OBJECT_STORE_PUBLIC_URL", "")
    object_store_region    = os.environ.get("OBJECT_STORE_REGION", "auto")
    # Airtable two-way script sync (VIDEOS table). Set all three to turn it on:
    # AIOS scripts mirror into Airtable, and new Airtable rows import as candidates.
    # Inert (no sync) until configured — the poll-only architecture means this is a
    # periodic sweep, never an Airtable webhook.
    airtable_api_key       = os.environ.get("AIRTABLE_API_KEY", "")
    airtable_base_id       = os.environ.get("AIRTABLE_BASE_ID", "")
    airtable_videos_table  = os.environ.get("AIRTABLE_VIDEOS_TABLE", "")
    # GTM contacts (the third table — GoHighLevel-modeled). One PAT (airtable_api_key)
    # reaches this base too; base id is non-secret deployment config.
    airtable_gtm_base_id   = os.environ.get("AIRTABLE_GTM_BASE_ID", "")
    airtable_gtm_contacts_table = os.environ.get("AIRTABLE_GTM_CONTACTS_TABLE", "Prospects")
    # Airtable-native production trigger: when this single-select field equals the
    # trigger value on a not-yet-produced row, Mavrick renders it (ONCE — guarded
    # on status so it never re-charges). Defaults match the owner's field/option.
    airtable_render_field  = os.environ.get("AIRTABLE_RENDER_FIELD", "Rendering")
    airtable_render_trigger = os.environ.get("AIRTABLE_RENDER_TRIGGER", "Queue Production")
    # Per-video production settings, synced both ways. Airtable shows human NAMES;
    # AIOS stores ids (avatar/voice from config.reel.avatars/voices) or the style
    # name (captions from config.reel.caption_styles). Field names default to the
    # owner's single-selects.
    airtable_avatar_field  = os.environ.get("AIRTABLE_AVATAR_FIELD", "Avatar")
    airtable_voice_field   = os.environ.get("AIRTABLE_VOICE_FIELD", "Voice")
    # LIVE-FOUND 2026-07-30: the owner's table calls this "Closed Caption Style", so the
    # default "Captions" never resolved and every render silently used the config default
    # instead of the owner's per-row pick (Clean/Hormozi/Maya).
    airtable_caption_field = os.environ.get("AIRTABLE_CAPTION_FIELD", "Closed Caption Style")
    # Linked-record picker into the Avatars/Voices library tables (owner mandate 2026-07-08:
    # choose from a real, searchable HeyGen catalog, not a hand-typed single-select). Distinct
    # field NAMES from the legacy select fields above — both can coexist on a live base; the
    # legacy Avatar/Voice fields are never touched/renamed/deleted by AIOS, only read as a
    # fallback for a record that hasn't been re-picked from the library yet.
    airtable_avatar_link_field = os.environ.get("AIRTABLE_AVATAR_LINK_FIELD", "Avatar (Library)")
    airtable_voice_link_field  = os.environ.get("AIRTABLE_VOICE_LINK_FIELD", "Voice (Library)")
    # Core synced columns are config too, so renaming a field in Airtable is a
    # setting change, not a silent 422 (the owner renamed Finished Asset URL ->
    # Finished Assets Folder, which broke the hardcoded push).
    airtable_title_field   = os.environ.get("AIRTABLE_TITLE_FIELD", "Overlay Text")
    airtable_script_field  = os.environ.get("AIRTABLE_SCRIPT_FIELD", "Script")
    # Carousel machine: the owner-editable slide breakdown on the CAROUSELS table
    # (---blocks; carousel spec §4). Same env-overridable posture as every field name.
    airtable_slides_field  = os.environ.get("AIRTABLE_SLIDES_FIELD", "Slides")
    # Carousel music bed (spec §9.5): the track name the owner types on the CAROUSELS
    # row. Empty = plain image carousel; set = ffmpeg slideshow video, IG-only.
    airtable_music_field   = os.environ.get("AIRTABLE_MUSIC_FIELD", "Music")
    airtable_motion_field  = os.environ.get("AIRTABLE_MOTION_FIELD", "Motion")
    airtable_engine_field  = os.environ.get("AIRTABLE_ENGINE_FIELD", "Engine")
    # Two-voice reels: which CAST member answers the phone. A name from reel.dialogue.cast,
    # never a provider voice id — see migration 35.
    airtable_voice2_field  = os.environ.get("AIRTABLE_VOICE2_FIELD", "Voice 2")
    airtable_status_field  = os.environ.get("AIRTABLE_STATUS_FIELD", "Status")
    airtable_finished_url_field = os.environ.get("AIRTABLE_FINISHED_URL_FIELD",
                                                 "Finished Assets Folder")
    # Attachment field — the finished MP4(s) land here (Airtable fetches them from
    # the R2 CDN URL). Gets BOTH the captioned final and the raw HeyGen render.
    airtable_assets_field  = os.environ.get("AIRTABLE_ASSETS_FIELD", "Finished Assets")
    # Auto Poster (Zernio) — deep SDK integration. Posts a finished reel to social.
    # Inert until ZERNIO_API_KEY is set (account ids auto-discovered via the SDK).
    zernio_api_key         = os.environ.get("ZERNIO_API_KEY", "")
    # The Zernio PROFILE this box posts as. On a box we built, the provisioner minted a profile of its own
    # and scripts/connector_handoff.py wrote its id here at first boot; the key is scoped to it, and the
    # client needs the id as well because the API takes profileId on its own calls. Empty on every other
    # box, which is exactly the behaviour before it existed.
    zernio_profile_id      = os.environ.get("ZERNIO_PROFILE_ID", "")
    zernio_timeout         = float(os.environ.get("ZERNIO_TIMEOUT", "30"))
    # WHERE A BUYER CANCELS MANAGED. Stripe's own customer-portal login page, which takes an email and mails
    # back a link — so cancelling needs NO credential of ours on a box its customer has root on, and no
    # service of ours in the path at all. Unset, the Managed page says to reply to the welcome email
    # instead, which is honest rather than a dead button.
    # THE SAME DOOR FOR EVERY BOX, so it ships as a default rather than a setting each box must be told.
    # It is Ownbox's Stripe customer portal, not a per-customer value and not a secret: a public login
    # page that mails the buyer a link. Env-overridable for a test account. Empty would mean every sold
    # box shows "reply to your welcome email" instead of a cancel button, which is the one thing a
    # subscription that renews itself must never do.
    managed_portal_url     = os.environ.get(
        "MANAGED_PORTAL_URL", "https://billing.stripe.com/p/login/fZufZb9028iOeWhao62Ry00")
    # Cockpit Post trigger: set this Airtable single-select to the trigger value on a
    # READY reel to publish it. Schedule field set → Zernio holds + releases on that date;
    # else it fires immediately (autopost_default_mode = "now"). Defaults match the owner's
    # live field/option ("Post to Social" = "Publish now"); env-overridable per clone.
    airtable_post_field    = os.environ.get("AIRTABLE_POST_FIELD", "Post to Social")
    airtable_post_trigger  = os.environ.get("AIRTABLE_POST_TRIGGER", "Publish now")
    airtable_vid_field     = os.environ.get("AIRTABLE_VID_FIELD", "ID")  # primary key = Airtable autoNumber `ID` (read-only; the owner references reels by it)
    airtable_caption_text_field = os.environ.get("AIRTABLE_CAPTION_TEXT_FIELD", "Caption")
    airtable_posting_time_field = os.environ.get("AIRTABLE_POSTING_TIME_FIELD", "Posting Time")
    # LIVE-FOUND 2026-07-30: the owner's VIDEOS table calls this "Date Posted", not
    # "Posted Time". _write_posted_time is best-effort, so the mismatch swallowed itself
    # and Airtable NEVER recorded when a video published — a silent hole in the audit
    # trail. Default now matches the live schema; env override keeps clones portable.
    airtable_posted_time_field  = os.environ.get("AIRTABLE_POSTED_TIME_FIELD", "Date Posted")
    # Per-row platform targeting (multipleSelects: Instagram/Facebook/Youtube/Tiktok/
    # Linkedin/Twitter-X). Present on the live table and previously IGNORED — publishing
    # fanned out to every connected account instead of the owner's per-row choice.
    # RENAMED on the board 2026-08-18: `Post2` -> `Platforms`. The old name said nothing
    # about what the column does and the owner read it as dead weight; the lookup is BY NAME,
    # so the rename and this default have to move together or the field silently reads as
    # empty (which fails open to every allowed platform — quiet, not loud).
    airtable_platforms_field = os.environ.get("AIRTABLE_PLATFORMS_FIELD", "Platforms")
    # Multi-scene reel machine (docs/MULTISCENE_REEL.md). Look = the Higgsfield reference
    # element that fixes wardrobe + set for EVERY scene; Scenes = the owner-editable split
    # (--- blocks, same contract as carousel Slides); Raw Videos = the per-scene workshop;
    # Approved = the gate that promotes an assembled cut into Finished Assets. Nothing
    # unapproved can ever reach the publishable slot.
    airtable_look_field      = os.environ.get("AIRTABLE_LOOK_FIELD", "Look")
    airtable_scenes_field    = os.environ.get("AIRTABLE_SCENES_FIELD", "Scenes")
    airtable_raw_images_field = os.environ.get("AIRTABLE_RAW_IMAGES_FIELD", "Raw Assets")
    airtable_raw_videos_field = os.environ.get("AIRTABLE_RAW_VIDEOS_FIELD", "Raw Videos")
    airtable_approved_field  = os.environ.get("AIRTABLE_APPROVED_FIELD", "Approved")
    airtable_approved_trigger = os.environ.get("AIRTABLE_APPROVED_TRIGGER", "Approve")
    # Lead-Magnet Machine fields (docs/LEAD_MAGNET_MACHINE_SPEC.md §4.1) — three fields on the
    # SAME VIDEOS table. No title field: the owner writes the body (Lead Magnet Content) + a
    # keyword; Mavrick derives the title and writes Lead Magnet URL back after Sanity publishes.
    airtable_keyword_field            = os.environ.get("AIRTABLE_KEYWORD_FIELD", "Keyword")
    airtable_leadmagnet_content_field = os.environ.get("AIRTABLE_LEADMAGNET_CONTENT_FIELD", "Lead Magnet Content")
    # ONE URL COLUMN (owner, 2026-08-05): `Brief URL` was renamed `URL` on the board and the
    # guide URLs folded into it, so both publish paths write the same receipt. A clone still on
    # two columns overrides this. The schema guard catches a box where `URL` does not exist.
    airtable_leadmagnet_url_field     = os.environ.get("AIRTABLE_LEADMAGNET_URL_FIELD", "URL")
    # The owner arms a row by setting this single-select to the trigger value below — the funnel's
    # single control surface. An armed row with Keyword + Lead Magnet Content filled is auto-
    # published + activated by the keyword-intake sweep (leadmagnet.airtable_intake). Off by default
    # in code (the sweep is gated); the field just has to exist on the VIDEOS table for the filter
    # to resolve. (@default: single-select "Publish Lead Magnet" with option "Publish now".)
    # `Publish Lead Magnet` -> `Publish to Website` -> `Post to Website` -> `Arm DM Keyword`
    # (owner, 2026-08-05 — three times in one afternoon, which is exactly why this is config and
    # not a literal). The final name is the honest one: this trigger does NOT put a page up, gate
    # 1 does. What it uniquely does is arm the keyword's Instagram comment/DM funnel, so someone
    # commenting `ENGINE` on a reel is sent the guide. The earlier names described gate 1's job.
    airtable_leadmagnet_trigger_field = os.environ.get("AIRTABLE_LEADMAGNET_TRIGGER_FIELD", "Arm DM Keyword")
    airtable_leadmagnet_trigger_value = os.environ.get("AIRTABLE_LEADMAGNET_TRIGGER_VALUE",
                                                   "Activate keyword")
    # WHICH KIND of WRITTEN row this is — the column that decides which section a piece lands
    # in, which URL column records it, and which Sanity keyword files it there. Without it the
    # lanes are told apart only by which trigger got armed, and one mis-click publishes an
    # article to /guides under a slug that is immutable the moment it is live (spec #302).
    #
    # BOTH VALUES ARE CONFIG, not literals in the code. The owner renames his own select options
    # — `Approve` -> `Post Now` -> `Post to Social`, `Thread` -> `X / Twitter`, and on 2026-08-05
    # `Brief` -> `Article`. Every one of those was a breaking change that reached code, and a
    # value written into a `setdefault` somewhere is the version that breaks silently: Airtable
    # 422s the write, the row never gets created, and the day looks merely quiet. A rename is now
    # one env var.
    airtable_type_field = os.environ.get("AIRTABLE_TYPE_FIELD", "Type")
    airtable_type_guide = os.environ.get("AIRTABLE_TYPE_GUIDE", "Guide")
    # The default for anything that is not a guide — what the daily job stamps on rows it
    # creates, and what a clone that never touches this column will always be.
    airtable_type_article = os.environ.get("AIRTABLE_TYPE_ARTICLE", "Article")
    # No schedule date on the reel → fire IMMEDIATELY (owner's production rule: "no date = post
    # now"). Default "now" so a Publish-now with no Posting Time publishes live; env-override to
    # "draft" on a clone that wants a staged-for-review step instead.
    autopost_default_mode  = os.environ.get("AUTOPOST_DEFAULT_MODE", "now")  # now|draft
    autopost_timezone      = os.environ.get("AUTOPOST_TIMEZONE", "America/Los_Angeles")
    # Platforms to post to (comma-sep, blank = every connected account). e.g. "tiktok,instagram"
    autopost_platforms     = os.environ.get("AUTOPOST_PLATFORMS", "")
    # Which platforms may EVER be a post target (fail-closed allowlist). Blank here falls back
    # to `autopost.publishable_platforms` in aios.config.yaml, then to the built-in default.
    # This exists so connecting a new platform in Zernio is a config edit rather than a code
    # change — it was hardcoded, so a freshly connected account was silently dropped as
    # "non-publishable" and any row that picked it fail-closed with nothing posted.
    autopost_publishable_platforms = os.environ.get("AUTOPOST_PUBLISHABLE_PLATFORMS", "")
    heygen_api_key         = os.environ.get("HEYGEN_API_KEY", "")
    heygen_api_base        = os.environ.get("HEYGEN_API_BASE", "https://api.heygen.com")
    scrapecreators_api_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
    scrapecreators_api_base = os.environ.get("SCRAPECREATORS_API_BASE",
                                             "https://api.scrapecreators.com")
    # GTM Engine vendors — flat-subscription (Instantly) or unlocking later
    # (Apollo/Hunter). Empty = that capability politely reports "not configured".
    instantly_api_key      = os.environ.get("INSTANTLY_API_KEY", "")
    instantly_api_base     = os.environ.get("INSTANTLY_API_BASE", "https://api.instantly.ai")
    instantly_campaign_id  = os.environ.get("INSTANTLY_CAMPAIGN_ID", "")
    # Resend send backend (gtm.send_backend: resend) — low-volume personalized
    # sends. GTM_FROM_EMAIL must be on a Resend-verified domain.
    resend_api_key         = os.environ.get("RESEND_API_KEY", "")
    gtm_from_email         = os.environ.get("GTM_FROM_EMAIL", "")
    # Lead-Magnet Machine (docs/LEAD_MAGNET_MACHINE_SPEC.md §5.1). Sanity robot token (read+write)
    # for publishing guides; the leadmagnet welcome from-address (falls back to gtm_from_email when
    # unset). Project/dataset/URLs live under the `leadmagnet:` config block, not env.
    sanity_api_token       = os.environ.get("SANITY_API_TOKEN", "")
    leadmagnet_from_email  = os.environ.get("LEADMAGNET_FROM_EMAIL", "")
    # W2.1 de-tenant: sender identity has NO baked-in default — the old fallback was the
    # owner's real name on every clone's cold email. Empty fails CLOSED at send time
    # (the AIOS_PHYSICAL_ADDRESS pattern), never falls back to anyone's identity.
    gtm_sender_name        = os.environ.get("GTM_SENDER_NAME", "")
    gtm_sender_title       = os.environ.get("GTM_SENDER_TITLE", "")      # signature line 1 suffix
    gtm_company_name       = os.environ.get("GTM_COMPANY_NAME", "")     # signature line 2
    gtm_cta_url            = os.environ.get("GTM_CTA_URL", "")          # the pitch's link target
    # Reply-To: cold sends go out from the verified subdomain (mail.nlvl.co) but replies
    # route to a MONITORED inbox (the root-domain Workspace box). Set once here, applied to
    # every send. Empty → no Reply-To header (falls back to the From address). No effect on
    # SPF/DKIM/DMARC (those key off From + return-path, never Reply-To).
    gtm_reply_to           = os.environ.get("GTM_REPLY_TO", "")
    # Two-touch sequence: how many days after touch 1 the follow-up (A2/B2) auto-sends,
    # and which ICP a lead falls into when none is specified (A = Slack-coworker,
    # B = speed-to-lead — the engine's original ICP, so it stays the default).
    gtm_followup_delay_days = int(os.environ.get("GTM_FOLLOWUP_DELAY_DAYS", "3"))
    gtm_default_segment    = (os.environ.get("GTM_DEFAULT_SEGMENT", "B").strip().upper()[:1] or "B")
    apollo_api_key         = os.environ.get("APOLLO_API_KEY", "")
    # Google Places (New) — the Places-native cold discovery surface (DEV1 handoff).
    # Server-side key: metered per request as vendor `google_places`.
    google_places_api_key  = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    hunter_api_key         = os.environ.get("HUNTER_API_KEY", "")
    # Second Hunter account for primary→backup failover (owner 2026-07-08). HUNTER_API_KEY2
    # is the DEFAULT (the subscription key); HUNTER_API_KEY is the BACKUP. hunter_client tries
    # KEY2 first and fails over to KEY on a hard monthly-quota exhaustion — so the two accounts'
    # separate allotments are both used before discovery hard-blocks. See hunter_client._keys().
    hunter_api_key2        = os.environ.get("HUNTER_API_KEY2", "")
    # Step-2 (name/domain → VERIFIED email) FREE providers behind the swappable
    # email-provider layer (docs/GTM_AUTOPILOT_SPEC.md). Tomba: domain-search + FREE
    # verifier; two keys (X-Tomba-Key + X-Tomba-Secret). Prospeo: enrich (needs a last
    # name). Config `gtm.email_providers` picks priority + failover order.
    tomba_api_key          = os.environ.get("TOMBA_API_KEY", "")
    tomba_api_secret       = os.environ.get("TOMBA_API_SECRET", "")
    prospeo_api_key        = os.environ.get("PROSPEO_API_KEY", "")
    dashboard_base_url     = os.environ.get("DASHBOARD_BASE_URL", "")
    # Prospect Lake ingest (docs/PROSPECTS_CONTRACT.md §3/§10) — the ONLY write path
    # to marketing.prospects. No Supabase credentials on this box, ever.
    mav_ingest_url         = os.environ.get("MAV_INGEST_URL", "")
    aios_ingest_secret     = os.environ.get("AIOS_INGEST_SECRET", "")
    # Demo-link mint (DEV1's /api/demo/mint). The URL derives from mav_ingest_url's
    # origin when unset; workspace_id is required and keeps the trigger INERT until set.
    mav_demo_mint_url      = os.environ.get("MAV_DEMO_MINT_URL", "")
    mav_workspace_id       = os.environ.get("MAV_WORKSPACE_ID", "")
    # Reply ingestion — IMAP poll of the GTM_REPLY_TO inbox (poll-only, no inbound webhook).
    # Inert (returns skipped) until all three are set. Folder defaults to INBOX.
    gtm_imap_host          = os.environ.get("GTM_IMAP_HOST", "")
    gtm_imap_user          = os.environ.get("GTM_IMAP_USER", "")
    gtm_imap_password      = os.environ.get("GTM_IMAP_PASSWORD", "")
    gtm_imap_folder        = os.environ.get("GTM_IMAP_FOLDER", "INBOX")
    # Meta Ads monitor (marketing.ads_machine) — READ-ONLY insights poll. Inert until
    # META_ACCESS_TOKEN + META_AD_ACCOUNT_ID are set (house convention). The token is a
    # long-lived System-User/Page token needing only `ads_read`; this module holds NO
    # create/update/activate call, so a leaked token here cannot spend — read-only by
    # construction (Phase 1 of marketing/ads_machine/SPEC.md).
    meta_access_token      = os.environ.get("META_ACCESS_TOKEN", "")
    meta_ad_account_id     = os.environ.get("META_AD_ACCOUNT_ID", "")   # numeric, no "act_" prefix
    meta_graph_version     = os.environ.get("META_GRAPH_VERSION", "v21.0")
    # Where the monitor posts its digest + dragger alerts. Falls back to the reel channel,
    # then an operator DM — a configured monitor is never silent.
    meta_ads_slack_channel_id = os.environ.get("META_ADS_SLACK_CHANNEL_ID", "")


settings = Settings()


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge — overlay wins; nested dicts merge, everything else replaces."""
    out = dict(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# W2.4 (audit C8): tenant values live in the TRACKED yaml, and deploy runs
# `git merge --ff-only` — so a clone that edited its config could never take an update
# again ("configure it or update it, not both"). The overlay closes that: a clone puts
# its tenant values in config/aios.config.local.yaml (GITIGNORED — never conflicts with
# upstream) and the tracked file stays pristine. Deep-merged over the base at load.
LOCAL_CONFIG_PATH = os.environ.get(
    "AIOS_CONFIGLOCAL_CONFIG_PATH", str(ROOT / "my" / "settings.yaml"))
# 2026-09-04: the overlay moved to my/settings.yaml (the one folder a buyer edits). A box that
# still has config/aios.config.local.yaml and no my/settings.yaml keeps reading the old file.
if not os.path.exists(LOCAL_CONFIG_PATH) and os.path.exists(str(ROOT / "config" / "aios.config.local.yaml")):
    LOCAL_CONFIG_PATH = str(ROOT / "config" / "aios.config.local.yaml")


@functools.lru_cache(maxsize=1)
def get_config() -> dict:
    """Parsed aios.config.yaml, deep-merged with the untracked local overlay when present
    (model tiering, cost ceiling, rate table, tenant bindings)."""
    import yaml
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    # PACK DEFAULTS ARE A MIDDLE LAYER (plan D6): tracked config < each installed pack's
    # manifest `config:` < my/settings.yaml. A pack carries every knob with its default and
    # a comment; the buyer's overlay holds only what they changed; an update to either the
    # tracked file or the pack never touches the buyer's choices. A broken pack is skipped
    # here (the doctor and the worker report it) so a bad manifest cannot brick boot.
    try:
        from core import packs as _packs
        for m in _packs.discover():
            cfg = _deep_merge(cfg, {"machines": {m["slug"]: m.get("config") or {}}})
    except Exception:  # pragma: no cover — config must load even if packs cannot
        pass
    try:
        if os.path.exists(LOCAL_CONFIG_PATH):
            with open(LOCAL_CONFIG_PATH) as f:
                cfg = _deep_merge(cfg, yaml.safe_load(f) or {})
    except Exception:  # pragma: no cover — a broken overlay must not brick boot
        pass
    return cfg


def as_bool(value, default: bool = False) -> bool:
    """Coerce a config/env value to bool, FAIL-CLOSED on ambiguity.

    YAML 1.1 coerces a bare ``off``/``on``/``no``/``yes`` to a Python bool, while a
    quoted ``"false"``/``"off"`` stays a *string* — and a naive ``bool(value)`` treats the
    non-empty string ``"false"`` as True, silently flipping a disabling value ON (a
    fail-OPEN gate; this is the exact class of bug behind the inbox kill switch). Only an
    explicit truthy token enables; any unrecognized value returns ``default``. Pass
    ``default=False`` for a gate that must fail closed (spend/write/send toggles).
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "on", "1"):
            return True
        if v in ("false", "no", "off", "0", ""):
            return False
    return default
