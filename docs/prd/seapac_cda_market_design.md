# Multi-Agent Negotiation Design

## Continuous Double Auction (CDA) Energy Market

This document describes the design of a Continuous Double Auction market
for multi-agent energy trading within an energy community.

------------------------------------------------------------------------

# Overview

CDA is a market mechanism where buyers and sellers submit bids and asks
continuously.

Transactions occur whenever: Bid Price ≥ Ask Price

The mechanism enables decentralized negotiation between prosumers.

------------------------------------------------------------------------

# Agent Roles

## Seller Agents

Prosumers with surplus electricity.

Strategy: - decide selling price - decide quantity - adjust price
dynamically

## Buyer Agents

Consumers with energy deficit.

Strategy: - determine maximum acceptable price - submit bids

## Market Coordinator Agent

Responsible for: - order book management - matching bids and asks -
executing trades

------------------------------------------------------------------------

# Market Data Structures

Order Book

Bid Table

  Agent   Price   Quantity
  ------- ------- ----------

Ask Table

  Agent   Price   Quantity
  ------- ------- ----------

------------------------------------------------------------------------

# Matching Algorithm

Step 1: Sort bids descending by price\
Step 2: Sort asks ascending by price\
Step 3: Match highest bid with lowest ask

Trade occurs if:

bid_price ≥ ask_price

Transaction price:

(mid_price) = (bid_price + ask_price)/2

------------------------------------------------------------------------

# Example Trade

Seller ask: 90\
Buyer bid: 100

Trade price = 95

------------------------------------------------------------------------

# Agent Strategy Examples

## SmartSeller Strategy

if demand_high: raise_price()

if surplus_high: lower_price()

## Buyer Strategy

if deficit_critical: increase_bid()

------------------------------------------------------------------------

# Integration with MESA

Simulation Step:

1.  Mesa updates energy state
2.  Agents submit bids/asks
3.  CDA market clears trades
4.  Results applied to Mesa
5.  KPIs evaluated

------------------------------------------------------------------------

# Advantages

-   decentralized decision making
-   price discovery
-   scalable multi-agent trading

------------------------------------------------------------------------

# Future Extensions

-   Reinforcement learning strategies
-   reputation-based trust scoring
-   dynamic pricing prediction
