"""Offline checks for reseller pricing, subscription charges, and payouts."""
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "sellerbottel_reseller_test"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret"

import motor.motor_asyncio as motor
from mongomock_motor import AsyncMongoMockClient

motor.AsyncIOMotorClient = AsyncMongoMockClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reseller_bot
import reseller_payout
import reseller_service
import reseller_signup
import reseller_contest
from db import db


def run(coro):
    return asyncio.run(coro)


def test_live_prices_and_editable_txt_follow_central_product_price():
    async def check():
        await db.settings.delete_many({})
        await db.products.delete_many({})
        await db.inventory_items.delete_many({})
        await db.settings.insert_one({"_id": "main", "reseller_wholesale_reduction_idr": 2000})
        await db.products.insert_one({"_id": "p1", "name": "Produk A", "active": True,
                                      "product_kind": "digital", "price_idr": 10000})
        await db.inventory_items.insert_one({"_id": "i1", "product_id": "p1", "status": "available"})
        bot = {"_id": "b1", "markups": {}, "default_markup_idr": 0}
        template = (await reseller_service.price_template(bot)).decode()
        assert "p1|Produk A|8000|10000" in template
        edited = template.replace("p1|Produk A|8000|10000", "p1|Produk A|8000|15000")
        parsed = await reseller_service.parse_price_template(bot, edited.encode())
        assert parsed["markups"] == {"p1": 5000}
        product = await db.products.find_one({"_id": "p1"})
        assert (await reseller_bot.current_price({**bot, **parsed}, product))["commission"] == 7000
        await db.products.update_one({"_id": "p1"}, {"$set": {"price_idr": 12000}})
        product = await db.products.find_one({"_id": "p1"})
        updated = await reseller_bot.current_price({**bot, **parsed}, product)
        assert updated == {"public": 12000, "wholesale": 10000, "selling": 17000, "commission": 7000}
        await db.inventory_items.delete_one({"_id": "i1"})
        assert "p1|" not in (await reseller_service.price_template(bot)).decode()
    run(check())


def test_subscription_charges_once_and_renews_monthly(monkeypatch):
    async def fake_webhook(bot):
        return None

    monkeypatch.setattr(reseller_service, "configure_webhook", fake_webhook)

    async def check():
        await db.reseller_bots.delete_many({})
        await db.bot_users.delete_many({})
        await db.reseller_payments.delete_many({})
        await db.settings.delete_many({})
        await db.settings.insert_one({"_id": "main", "reseller_bot_price_idr": 80000,
                                      "reseller_admin_fee_idr": 10000,
                                      "reseller_platform_fee_idr": 10000})
        await db.bot_users.insert_one({"telegram_id": 42, "balance_idr": 250000})
        bot = {"_id": "sub1", "owner_tid": 42, "admin_tid": 42,
               "username": "reseller_bot", "status": "pending_payment", "cycle_id": "cycle1",
               "fees": {"bot_price": 80000, "admin_fee": 10000, "platform_fee": 10000, "total": 100000}}
        await db.reseller_bots.insert_one(bot)
        assert await reseller_service.activate_paid_bot(bot)
        first = await db.reseller_bots.find_one({"_id": "sub1"})
        assert first["status"] == "active" and first["expires_at"]
        assert (await db.bot_users.find_one({"telegram_id": 42}))["balance_idr"] == 150000
        assert not await reseller_service.activate_paid_bot(first)
        assert not await reseller_service.activate_paid_bot(bot)
        renewed = await reseller_service.begin_renewal(first)
        assert await reseller_service.activate_paid_bot(renewed)
        assert not await reseller_service.activate_paid_bot(renewed)
        assert (await db.bot_users.find_one({"telegram_id": 42}))["balance_idr"] == 50000
        assert await db.reseller_payments.count_documents({"bot_id": "sub1"}) == 2
        assert (await db.reseller_bots.find_one({"_id": "sub1"}))["expires_at"] > first["expires_at"]
    run(check())


def test_ewallet_payout_has_fee_and_is_only_requested_once():
    async def check():
        await db.reseller_bots.delete_many({})
        await db.reseller_commissions.delete_many({})
        await db.reseller_payouts.delete_many({})
        bot = {"_id": "pay1", "owner_tid": 42, "username": "reseller_bot",
               "payout_threshold_idr": 50000,
               "payout_destination": {"type": "EWALLET", "provider": "DANA",
                                      "number": "08123456789", "name": "Owner"}}
        await db.reseller_bots.insert_one(bot)
        await db.reseller_commissions.insert_many([
            {"_id": "o1", "bot_id": "pay1", "amount": 30000, "status": "pending_payout"},
            {"_id": "o2", "bot_id": "pay1", "amount": 30000, "status": "pending_payout"},
        ])
        payout = await reseller_payout.maybe_request_payout(bot)
        assert payout["amount"] == 60000 and payout["transfer_fee"] == 2500
        assert payout["net_amount"] == 57500
        assert not await reseller_payout.maybe_request_payout(bot)
        assert await db.reseller_payouts.count_documents({}) == 1
    run(check())


def test_reseller_checkout_charges_sale_price_and_records_margin(monkeypatch):
    sent = []

    async def fake_send(bot, tid, message, keyboard=None):
        sent.append(message)
        return {"ok": True}

    monkeypatch.setattr(reseller_bot, "send", fake_send)

    async def check():
        await db.settings.delete_many({})
        await db.products.delete_many({})
        await db.reseller_bots.delete_many({})
        await db.bot_users.delete_many({})
        await db.purchases.delete_many({})
        await db.reseller_commissions.delete_many({})
        await db.settings.insert_one({"_id": "main", "reseller_wholesale_reduction_idr": 2000})
        await db.products.insert_one({"_id": "sale-p", "name": "Link A", "active": True,
                                      "delivery_type": "link", "content": "https://example.test/a",
                                      "price_idr": 10000, "price_usd": 1})
        bot = {"_id": "sale-bot", "owner_tid": 77, "admin_tid": 77,
               "username": "reseller_bot", "status": "active", "markups": {"sale-p": 5000},
               "expires_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()}
        await db.reseller_bots.insert_one(bot)
        await db.bot_users.insert_one({"telegram_id": 88, "username": "buyer", "currency": "IDR",
                                       "balance_idr": 20000, "balance_usd": 0,
                                       "cart": [], "frozen": False})
        await reseller_bot.buy(bot, 88, "sale-p")
        order = await db.purchases.find_one({"reseller_bot_id": "sale-bot"})
        assert order["status"] == "delivered"
        assert order["total"] == 15000 and order["reseller_wholesale"] == 8000
        assert order["reseller_margin"] == 7000
        assert (await db.bot_users.find_one({"telegram_id": 88}))["balance_idr"] == 5000
        assert (await db.reseller_commissions.find_one({"_id": order["_id"]}))["amount"] == 7000
        assert any("berhasil dikirim" in message for message in sent)
    run(check())


def test_inactive_after_14_days_without_paid_sales_and_no_refund(monkeypatch):
    messages = []

    async def fake_message(tid, message, *args, **kwargs):
        messages.append((tid, message))

    async def fake_admin(message):
        return None

    monkeypatch.setattr(reseller_signup, "send_message", fake_message)
    monkeypatch.setattr(reseller_signup, "notify_admin", fake_admin)

    async def check():
        await db.reseller_bots.delete_many({})
        await db.purchases.delete_many({})
        await db.reseller_payouts.delete_many({})
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=15)).isoformat()
        recent = (now - timedelta(days=2)).isoformat()
        expires = (now + timedelta(days=10)).isoformat()
        await db.reseller_bots.insert_many([
            {"_id": "idle", "owner_tid": 1, "username": "idle_bot", "status": "active",
             "last_cycle_paid_at": old, "expires_at": expires},
            {"_id": "selling", "owner_tid": 2, "username": "selling_bot", "status": "active",
             "last_cycle_paid_at": old, "expires_at": expires},
            {"_id": "renewed", "owner_tid": 3, "username": "renewed_bot", "status": "active",
             "last_cycle_paid_at": recent, "expires_at": expires},
        ])
        await db.purchases.insert_many([
            {"_id": "recent-sale", "reseller_bot_id": "selling", "status": "delivered",
             "created_at": recent},
            {"_id": "unpaid", "reseller_bot_id": "idle", "status": "failed",
             "created_at": recent},
        ])
        await reseller_signup.scan_subscriptions()
        assert (await db.reseller_bots.find_one({"_id": "idle"}))["status"] == "inactive_no_sales"
        assert (await db.reseller_bots.find_one({"_id": "selling"}))["status"] == "active"
        assert (await db.reseller_bots.find_one({"_id": "renewed"}))["status"] == "active"
        assert len(messages) == 1 and "tidak dikembalikan" in messages[0][1]
        await reseller_signup.scan_subscriptions()
        assert len(messages) == 1

    run(check())


def test_contest_combines_owner_sales_and_requires_target(monkeypatch):
    notifications = []

    async def fake_admin(message):
        notifications.append(message)

    async def fake_message(tid, message):
        notifications.append(message)

    monkeypatch.setattr(reseller_contest, "notify_admin", fake_admin)
    monkeypatch.setattr(reseller_contest, "send_message", fake_message)

    async def check():
        await db.reseller_bots.delete_many({})
        await db.purchases.delete_many({})
        await db.reseller_contests.delete_many({})
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=2)).isoformat()
        end = (now - timedelta(days=1)).isoformat()
        inside = (now - timedelta(days=1, hours=12)).isoformat()
        outside = (now - timedelta(hours=12)).isoformat()
        await db.reseller_bots.insert_many([
            {"_id": "b1", "owner_tid": 1, "username": "one"},
            {"_id": "b2", "owner_tid": 1, "username": "two"},
            {"_id": "b3", "owner_tid": 2, "username": "three"},
        ])
        await db.purchases.insert_many([
            {"_id": "c1", "reseller_bot_id": "b1", "status": "delivered", "currency": "IDR", "total": 600000, "paid_at": inside},
            {"_id": "c2", "reseller_bot_id": "b2", "status": "service_waiting", "currency": "IDR", "total": 500000, "paid_at": inside},
            {"_id": "c3", "reseller_bot_id": "b3", "status": "delivered", "currency": "IDR", "total": 900000, "paid_at": inside},
            {"_id": "c4", "reseller_bot_id": "b3", "status": "refunded", "currency": "IDR", "total": 1000000, "paid_at": inside},
            {"_id": "c5", "reseller_bot_id": "b3", "status": "delivered", "currency": "IDR", "total": 1000000, "paid_at": outside},
        ])
        contest = await reseller_contest.create_contest("Kontes September", start, end, 1000000, 250000)
        ranking = await reseller_contest.leaderboard(contest)
        assert ranking[0]["owner_tid"] == 1 and ranking[0]["sales_idr"] == 1100000
        assert ranking[0]["eligible"] and not ranking[1]["eligible"]
        result = await reseller_contest.settle_contest(contest)
        assert result["winner"]["owner_tid"] == 1
        assert (await db.reseller_contests.find_one({"_id": contest["_id"]}))["status"] == "winner_pending_transfer"
        assert not await reseller_contest.settle_contest(contest)
        assert len(notifications) == 2

    run(check())
