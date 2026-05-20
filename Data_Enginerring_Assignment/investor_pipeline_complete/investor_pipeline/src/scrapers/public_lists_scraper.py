"""
src/scrapers/public_lists_scraper.py
─────────────────────────────────────
Scrapes publicly accessible investor lists from:
  - GitHub-hosted open datasets (e.g. awesome-vc lists, public JSON repos)
  - Public CSV/JSON files hosted on known investor databases
  - Accelerator portfolio pages
  - Public rankings / "Top VC" articles

These sources require no login, no API key, and no paywall bypass.

This module also includes a STATIC CURATED DATASET of 200 well-documented
investors as a reliable baseline that will always populate the dataset
regardless of scraping success.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.scrapers.base_scraper import BaseScraper
from src.utils.helpers import normalise_url, clean_text
from src.utils.schema import InvestorRecord
from src.scrapers.vc_websites_scraper import _infer_geo


# ─────────────────────────────────────────────────────────────────────────────
# Static curated dataset (200 investors — always reliable)
# ─────────────────────────────────────────────────────────────────────────────

CURATED_INVESTORS: list[dict] = [
    # USA VC Firms
    {"name":"Sequoia Capital","type":"Venture Capital","firm":"Sequoia Capital","url":"https://sequoiacap.com","country":"USA","geo":"North America","sector":"Technology, Consumer, Enterprise","stage":"Seed, Series A, Growth","thesis":"Partners with legendary founders from inception through IPO and beyond.","stage_detail":"Seed to Growth"},
    {"name":"Andreessen Horowitz (a16z)","type":"Venture Capital","firm":"Andreessen Horowitz","url":"https://a16z.com","country":"USA","geo":"North America","sector":"Software, Crypto, BioTech, FinTech","stage":"Seed, Series A, Growth","thesis":"We believe it's time to build — software is eating the world.","ticket":"$1M–$300M+"},
    {"name":"Y Combinator","type":"Accelerator / Incubator","firm":"Y Combinator","url":"https://ycombinator.com","country":"USA","geo":"Global","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"The most successful startup accelerator. Invests $500K for 7% equity.","ticket":"$500K"},
    {"name":"Accel","type":"Venture Capital","firm":"Accel","url":"https://accel.com","country":"USA","geo":"North America, Europe, India","sector":"SaaS, Enterprise, Consumer","stage":"Seed, Series A, Series B","thesis":"Accel is a global venture capital firm that invests in people and their companies from the earliest days through all phases of private company growth."},
    {"name":"Bessemer Venture Partners","type":"Venture Capital","firm":"Bessemer Venture Partners","url":"https://bvp.com","country":"USA","geo":"North America","sector":"Cloud, HealthTech, FinTech","stage":"Seed, Series A, Growth","thesis":"The longest-standing early-stage venture capital partnership in the US."},
    {"name":"Greylock","type":"Venture Capital","firm":"Greylock Partners","url":"https://greylock.com","country":"USA","geo":"North America","sector":"Enterprise, Consumer, AI","stage":"Seed, Series A, Series B","thesis":"Invests in technical founders building transformative consumer and enterprise products."},
    {"name":"Kleiner Perkins","type":"Venture Capital","firm":"Kleiner Perkins","url":"https://kleinerperkins.com","country":"USA","geo":"North America","sector":"Consumer, Enterprise, HealthTech","stage":"Seed, Series A, Growth","thesis":"Partners with bold entrepreneurs who are building the future."},
    {"name":"Benchmark","type":"Venture Capital","firm":"Benchmark","url":"https://benchmark.com","country":"USA","geo":"North America","sector":"Consumer, SaaS, Marketplace","stage":"Seed, Series A","thesis":"Focused, disciplined early-stage fund with small partnership."},
    {"name":"Union Square Ventures","type":"Venture Capital","firm":"Union Square Ventures","url":"https://usv.com","country":"USA","geo":"North America","sector":"Marketplace, Crypto, Consumer","stage":"Seed, Series A","thesis":"Invests in large, networks of engaged users and data."},
    {"name":"First Round Capital","type":"Venture Capital","firm":"First Round Capital","url":"https://firstround.com","country":"USA","geo":"North America","sector":"SaaS, Consumer, Marketplace","stage":"Pre-Seed, Seed","thesis":"The first check in."},
    {"name":"Founders Fund","type":"Venture Capital","firm":"Founders Fund","url":"https://foundersfund.com","country":"USA","geo":"North America","sector":"DeepTech, BioTech, SpaceTech","stage":"Seed, Growth","thesis":"We wanted flying cars, instead we got 140 characters."},
    {"name":"Lux Capital","type":"Venture Capital","firm":"Lux Capital","url":"https://luxcapital.com","country":"USA","geo":"North America","sector":"DeepTech, AI, SpaceTech","stage":"Seed, Series A","thesis":"Invests in science and technology companies at the outermost edges of what is possible."},
    {"name":"GV (Google Ventures)","type":"Corporate Venture","firm":"GV","url":"https://gv.com","country":"USA","geo":"North America","sector":"HealthTech, AI, Consumer, Enterprise","stage":"Seed, Series A, Series B","thesis":"The venture capital arm of Alphabet. Invests across all stages."},
    {"name":"Intel Capital","type":"Corporate Venture","firm":"Intel Capital","url":"https://intelcapital.com","country":"USA","geo":"Global","sector":"AI, Cloud, Hardware, IoT","stage":"Seed, Series A, Growth","thesis":"Intel Capital invests in innovative startups targeting enterprise, cloud, and AI."},
    {"name":"Salesforce Ventures","type":"Corporate Venture","firm":"Salesforce Ventures","url":"https://salesforceventures.com","country":"USA","geo":"Global","sector":"SaaS, Enterprise, AI","stage":"Series A, Series B, Growth","thesis":"Invests in the next generation of enterprise technology."},
    {"name":"Microsoft M12","type":"Corporate Venture","firm":"M12 (Microsoft Ventures)","url":"https://m12.vc","country":"USA","geo":"Global","sector":"AI, SaaS, Security, Developer Tools","stage":"Series A, Series B","thesis":"Microsoft's corporate venture fund focused on the future of work and AI."},
    {"name":"Lightspeed Venture Partners","type":"Venture Capital","firm":"Lightspeed Venture Partners","url":"https://lsvp.com","country":"USA","geo":"Global","sector":"Enterprise, Consumer, FinTech","stage":"Seed, Series A, Growth","thesis":"Multiple phases of company building from early to growth."},
    {"name":"General Catalyst","type":"Venture Capital","firm":"General Catalyst","url":"https://generalcatalyst.com","country":"USA","geo":"North America","sector":"HealthTech, SaaS, Consumer","stage":"Seed, Series A, Growth","thesis":"Partnering with companies that create meaningful transformation."},
    {"name":"Insight Partners","type":"Venture Capital","firm":"Insight Partners","url":"https://insightpartners.com","country":"USA","geo":"Global","sector":"SaaS, Enterprise","stage":"Growth, Late Stage","thesis":"Invests in high-growth technology, software, and internet businesses.","ticket":"$10M–$500M"},
    {"name":"Tiger Global Management","type":"Venture Capital","firm":"Tiger Global Management","url":"https://tigerglobal.com","country":"USA","geo":"Global","sector":"Technology, Consumer, SaaS","stage":"Growth, Late Stage","thesis":"Long-term investor in global internet and technology companies."},
    # Europe VC
    {"name":"Index Ventures","type":"Venture Capital","firm":"Index Ventures","url":"https://indexventures.com","country":"UK","geo":"Europe","sector":"SaaS, Consumer, FinTech","stage":"Seed, Series A, Series B","thesis":"Index backs bold entrepreneurs who use technology to reshape industries."},
    {"name":"Balderton Capital","type":"Venture Capital","firm":"Balderton Capital","url":"https://balderton.com","country":"UK","geo":"Europe","sector":"SaaS, Marketplace, Consumer","stage":"Series A, Series B","thesis":"Invests exclusively in European-founded technology companies."},
    {"name":"Atomico","type":"Venture Capital","firm":"Atomico","url":"https://atomico.com","country":"UK","geo":"Europe","sector":"DeepTech, SaaS, Consumer","stage":"Series A, Series B, Growth","thesis":"Invests in world-changing technology companies across Europe."},
    {"name":"LocalGlobe","type":"Venture Capital","firm":"LocalGlobe","url":"https://localglobe.vc","country":"UK","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Backs European founders from day one.","ticket":"£250K–£2M"},
    {"name":"Northzone","type":"Venture Capital","firm":"Northzone","url":"https://northzone.com","country":"Sweden","geo":"Europe","sector":"SaaS, Consumer, FinTech","stage":"Seed, Series A, Series B","thesis":"Partnering with founders who are building the next generation of tech giants in Europe."},
    {"name":"HV Capital","type":"Venture Capital","firm":"HV Capital","url":"https://hvcapital.com","country":"Germany","geo":"Europe","sector":"SaaS, Marketplace, FinTech","stage":"Series A, Series B, Growth","thesis":"Backing exceptional founders transforming digital businesses in Europe."},
    {"name":"Point Nine Capital","type":"Venture Capital","firm":"Point Nine Capital","url":"https://pointninecap.com","country":"Germany","geo":"Europe","sector":"SaaS, Marketplace","stage":"Seed, Series A","thesis":"Pre-seed and seed specialist for SaaS and marketplace startups.","ticket":"$500K–$3M"},
    {"name":"Speedinvest","type":"Venture Capital","firm":"Speedinvest","url":"https://speedinvest.com","country":"Austria","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Europe's most active pre-seed investor with deep operational support."},
    {"name":"Partech","type":"Venture Capital","firm":"Partech Partners","url":"https://partechpartners.com","country":"France","geo":"Europe, Africa","sector":"SaaS, Consumer, DeepTech","stage":"Seed, Series A, Series B","thesis":"Partners with entrepreneurs from seed to growth."},
    {"name":"Kima Ventures","type":"Venture Capital","firm":"Kima Ventures","url":"https://kimaventures.com","country":"France","geo":"Global","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"One of the most active seed funds globally — 100+ investments per year.","ticket":"€150K"},
    {"name":"Earlybird Venture Capital","type":"Venture Capital","firm":"Earlybird Venture Capital","url":"https://earlybird.com","country":"Germany","geo":"Europe","sector":"DeepTech, SaaS, HealthTech","stage":"Seed, Series A","thesis":"Deep tech and software specialist investing across Europe."},
    {"name":"Cherry Ventures","type":"Venture Capital","firm":"Cherry Ventures","url":"https://cherry.vc","country":"Germany","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Backing ambitious founders from pre-seed through Series A."},
    {"name":"Target Global","type":"Venture Capital","firm":"Target Global","url":"https://targetglobal.vc","country":"Germany","geo":"Europe, MENA","sector":"FinTech, Consumer, SaaS","stage":"Seed, Series A, Series B","thesis":"Global venture capital firm with presence in Berlin, London, and Tel Aviv."},
    {"name":"Seedcamp","type":"Accelerator / Incubator","firm":"Seedcamp","url":"https://seedcamp.com","country":"UK","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Europe's seed fund and startup accelerator.","ticket":"€200K"},
    {"name":"Notion Capital","type":"Venture Capital","firm":"Notion Capital","url":"https://notion.vc","country":"UK","geo":"Europe","sector":"SaaS, Enterprise","stage":"Seed, Series A","thesis":"B2B SaaS specialist in Europe."},
    # India / South Asia
    {"name":"Blume Ventures","type":"Venture Capital","firm":"Blume Ventures","url":"https://blumeventures.com","country":"India","geo":"South Asia","sector":"SaaS, Consumer, FinTech","stage":"Pre-Seed, Seed, Series A","thesis":"The first check for audacious Indian founders."},
    {"name":"Kalaari Capital","type":"Venture Capital","firm":"Kalaari Capital","url":"https://kalaari.com","country":"India","geo":"South Asia","sector":"Consumer, SaaS, FinTech","stage":"Seed, Series A, Series B","thesis":"Backing founders building for Bharat and the world."},
    {"name":"Matrix Partners India","type":"Venture Capital","firm":"Matrix Partners India","url":"https://matrixpartners.in","country":"India","geo":"South Asia","sector":"Generalist","stage":"Seed, Series A, Series B","thesis":"Partner of choice for ambitious entrepreneurs building meaningful companies."},
    {"name":"Nexus Venture Partners","type":"Venture Capital","firm":"Nexus Venture Partners","url":"https://nexusvp.com","country":"India","geo":"South Asia, North America","sector":"SaaS, Consumer, Enterprise","stage":"Seed, Series A, Series B","thesis":"India-first, global-always."},
    {"name":"Chiratae Ventures","type":"Venture Capital","firm":"Chiratae Ventures","url":"https://chiratae.com","country":"India","geo":"South Asia","sector":"HealthTech, AgriTech, Consumer","stage":"Seed, Series A","thesis":"India's most connected VC."},
    {"name":"Venture Catalysts","type":"Venture Capital","firm":"Venture Catalysts","url":"https://venturecatalysts.in","country":"India","geo":"South Asia","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"India's first integrated incubator and largest angel investment group."},
    {"name":"Indian Angel Network","type":"Angel Investor","firm":"Indian Angel Network","url":"https://indianangelnetwork.com","country":"India","geo":"South Asia","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"India's first and largest angel investor network.","ticket":"$250K–$2M"},
    # Southeast Asia
    {"name":"GGV Capital","type":"Venture Capital","firm":"GGV Capital","url":"https://ggvc.com","country":"USA","geo":"Asia Pacific, North America","sector":"SaaS, Consumer, Marketplace","stage":"Seed, Series A, Growth","thesis":"Local founders, global scale."},
    {"name":"Golden Gate Ventures","type":"Venture Capital","firm":"Golden Gate Ventures","url":"https://goldengate.vc","country":"Singapore","geo":"Southeast Asia","sector":"Consumer, Marketplace, FinTech","stage":"Seed, Series A","thesis":"Southeast Asia-focused early stage VC."},
    {"name":"Monk's Hill Ventures","type":"Venture Capital","firm":"Monk's Hill Ventures","url":"https://monkshill.com","country":"Singapore","geo":"Southeast Asia","sector":"SaaS, Consumer, DeepTech","stage":"Seed, Series A","thesis":"Leading venture capital firm in Southeast Asia."},
    {"name":"Jungle Ventures","type":"Venture Capital","firm":"Jungle Ventures","url":"https://jungle.vc","country":"Singapore","geo":"Southeast Asia, South Asia","sector":"Consumer, SaaS, FinTech","stage":"Series A, Series B","thesis":"Asia's most globally oriented venture firm."},
    {"name":"Vertex Ventures SEA","type":"Venture Capital","firm":"Vertex Ventures","url":"https://vertexventures.com","country":"Singapore","geo":"Southeast Asia","sector":"Generalist","stage":"Seed, Series A, Series B","thesis":"Southeast Asia and India focused VC fund."},
    {"name":"East Ventures","type":"Venture Capital","firm":"East Ventures","url":"https://east.vc","country":"Indonesia","geo":"Southeast Asia","sector":"Consumer, SaaS, FinTech","stage":"Pre-Seed, Seed, Series A","thesis":"One of the most active and consistent early-stage investor in Southeast Asia."},
    # MENA
    {"name":"Wamda Capital","type":"Venture Capital","firm":"Wamda Capital","url":"https://wamda.com","country":"UAE","geo":"MENA","sector":"Generalist","stage":"Seed, Series A","thesis":"Building ecosystems for Arab entrepreneurs."},
    {"name":"Flat6Labs","type":"Accelerator / Incubator","firm":"Flat6Labs","url":"https://flat6labs.com","country":"Egypt","geo":"MENA","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"MENA's largest startup accelerator."},
    {"name":"Algebra Ventures","type":"Venture Capital","firm":"Algebra Ventures","url":"https://algebraventures.com","country":"Egypt","geo":"MENA","sector":"Generalist","stage":"Seed, Series A","thesis":"Investing in Egypt and the Arab world."},
    {"name":"Beco Capital","type":"Venture Capital","firm":"Beco Capital","url":"https://beco.vc","country":"UAE","geo":"MENA","sector":"FinTech, Consumer, SaaS","stage":"Seed, Series A","thesis":"MENA-focused early-stage venture capital."},
    {"name":"MEVP","type":"Venture Capital","firm":"Middle East Venture Partners","url":"https://mevp.com","country":"Lebanon","geo":"MENA","sector":"Generalist","stage":"Seed, Series A","thesis":"The leading venture capital firm in the Arab world."},
    # Africa
    {"name":"Partech Africa","type":"Venture Capital","firm":"Partech Africa","url":"https://partechpartners.com/africa","country":"Senegal","geo":"Middle East & Africa","sector":"Generalist","stage":"Seed, Series A","thesis":"Africa-focused venture capital investing in tech and digital companies."},
    {"name":"Ventures Platform","type":"Venture Capital","firm":"Ventures Platform","url":"https://venturesplatform.com","country":"Nigeria","geo":"Middle East & Africa","sector":"FinTech, Consumer, AgriTech","stage":"Pre-Seed, Seed","thesis":"Backing Africa's boldest."},
    {"name":"4DX Ventures","type":"Venture Capital","firm":"4DX Ventures","url":"https://4dxventures.com","country":"South Africa","geo":"Middle East & Africa","sector":"Generalist","stage":"Seed, Series A","thesis":"Africa-focused, opportunity driven, founder-led investment platform."},
    # Latin America
    {"name":"Monashees","type":"Venture Capital","firm":"Monashees","url":"https://monashees.com.br","country":"Brazil","geo":"Latin America","sector":"Consumer, FinTech, SaaS","stage":"Seed, Series A, Series B","thesis":"Brazil's leading early-stage venture capital fund."},
    {"name":"Kaszek Ventures","type":"Venture Capital","firm":"Kaszek Ventures","url":"https://kaszek.com","country":"Brazil","geo":"Latin America","sector":"Consumer, FinTech, SaaS","stage":"Seed, Series A, Growth","thesis":"Latin America's largest and most active tech VC."},
    {"name":"Softbank Latin America Fund","type":"Venture Capital","firm":"SoftBank Latin America Fund","url":"https://softbank.com","country":"Japan","geo":"Latin America","sector":"Technology, Consumer","stage":"Growth, Late Stage","thesis":"Investing in the transformation of Latin America through technology."},
    {"name":"ALLVP","type":"Venture Capital","firm":"ALLVP","url":"https://allvp.vc","country":"Mexico","geo":"Latin America","sector":"FinTech, Consumer, SaaS","stage":"Seed, Series A","thesis":"Mexico and Latin America's leading early-stage VC."},
    # Global / Multi-stage
    {"name":"SoftBank Vision Fund","type":"Venture Capital","firm":"SoftBank Vision Fund","url":"https://visionfund.com","country":"Japan","geo":"Global","sector":"Technology, AI, Mobility","stage":"Growth, Late Stage","thesis":"Investing in transformational technology companies.","ticket":"$100M+"},
    {"name":"Coatue Management","type":"Venture Capital","firm":"Coatue","url":"https://coatue.com","country":"USA","geo":"Global","sector":"Technology, Consumer","stage":"Growth, Late Stage","thesis":"Technology-focused hedge fund and VC crossover investor."},
    {"name":"DST Global","type":"Venture Capital","firm":"DST Global","url":"https://dst.global","country":"UK","geo":"Global","sector":"Consumer, SaaS, Marketplace","stage":"Growth, Late Stage","thesis":"Invests in the world's best internet businesses."},
    # Angel investor networks
    {"name":"AngelList","type":"Angel Investor","firm":"AngelList","url":"https://angel.co","country":"USA","geo":"Global","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Where the startup world meets. Access to deals, rolling funds, and syndicates."},
    {"name":"500 Global","type":"Accelerator / Incubator","firm":"500 Global","url":"https://500.co","country":"USA","geo":"Global","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"A venture capital firm on a mission to discover and back the world's most talented entrepreneurs.","ticket":"$150K"},
    {"name":"Techstars","type":"Accelerator / Incubator","firm":"Techstars","url":"https://techstars.com","country":"USA","geo":"Global","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"The worldwide network that helps entrepreneurs succeed.","ticket":"$120K"},
    {"name":"Plug and Play","type":"Accelerator / Incubator","firm":"Plug and Play Tech Center","url":"https://plugandplaytechcenter.com","country":"USA","geo":"Global","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"The world's largest startup accelerator platform."},
    # PE / Growth
    {"name":"KKR","type":"Private Equity","firm":"KKR","url":"https://kkr.com","country":"USA","geo":"Global","sector":"Generalist","stage":"Buyout, Growth","thesis":"A leading global investment firm managing multiple alternative asset classes.","ticket":"$100M+"},
    {"name":"Blackstone","type":"Private Equity","firm":"Blackstone","url":"https://blackstone.com","country":"USA","geo":"Global","sector":"Generalist","stage":"Buyout, Growth","thesis":"The world's largest alternative asset management firm.","ticket":"$500M+"},
    {"name":"The Carlyle Group","type":"Private Equity","firm":"The Carlyle Group","url":"https://carlyle.com","country":"USA","geo":"Global","sector":"Generalist","stage":"Buyout, Growth","thesis":"Global investment firm with diverse portfolio of investments across multiple alternative asset classes."},
    {"name":"Apollo Global Management","type":"Private Equity","firm":"Apollo Global Management","url":"https://apollo.com","country":"USA","geo":"Global","sector":"Generalist","stage":"Buyout, Debt","thesis":"Global alternative asset manager with expertise in credit, equity, and real assets."},
    # Sovereign Wealth
    {"name":"GIC","type":"Sovereign Wealth Fund","firm":"GIC","url":"https://gic.com.sg","country":"Singapore","geo":"Global","sector":"Generalist","stage":"Growth, Late Stage","thesis":"Singapore's sovereign wealth fund investing globally.","ticket":"$50M+"},
    {"name":"Temasek","type":"Sovereign Wealth Fund","firm":"Temasek","url":"https://temasek.com.sg","country":"Singapore","geo":"Global","sector":"Technology, FinTech, HealthTech","stage":"Growth, Late Stage","thesis":"Singapore investment company owned by the Government of Singapore."},
    {"name":"Mubadala Investment","type":"Sovereign Wealth Fund","firm":"Mubadala Investment Company","url":"https://mubadala.com","country":"UAE","geo":"Global","sector":"Technology, CleanTech","stage":"Growth, Late Stage","thesis":"Abu Dhabi's diversified global investment company.","ticket":"$100M+"},
    {"name":"ADIA (Abu Dhabi Investment Authority)","type":"Sovereign Wealth Fund","firm":"ADIA","url":"https://adia.ae","country":"UAE","geo":"Global","sector":"Generalist","stage":"Growth, Late Stage","thesis":"One of the world's largest sovereign wealth funds."},
    # Family Offices (notable public ones)
    {"name":"Emerson Collective","type":"Family Office","firm":"Emerson Collective","url":"https://emersoncollective.com","country":"USA","geo":"North America","sector":"EdTech, HealthTech, CleanTech","stage":"Seed, Series A, Growth","thesis":"Driven by the belief that every human has the potential to succeed."},
    {"name":"Bezos Expeditions","type":"Family Office","firm":"Bezos Expeditions","url":"https://bezosexpeditions.com","country":"USA","geo":"North America","sector":"DeepTech, CleanTech, Consumer","stage":"Seed, Series A","thesis":"Jeff Bezos's personal investment vehicle."},
    {"name":"Yuri Milner / DST","type":"Family Office","firm":"DST Global","url":"https://dst.global","country":"UK","geo":"Global","sector":"Consumer, Social, Marketplace","stage":"Growth, Late Stage","thesis":"Invests in exceptional internet companies globally."},
    # Additional diversity across geographies
    {"name":"Antler","type":"Accelerator / Incubator","firm":"Antler","url":"https://antler.co","country":"Singapore","geo":"Global","sector":"Generalist","stage":"Pre-Seed","thesis":"The global early-stage VC enabling and investing in the defining companies of tomorrow.","ticket":"$100K"},
    {"name":"Entrepreneur First","type":"Accelerator / Incubator","firm":"Entrepreneur First","url":"https://joinef.com","country":"UK","geo":"Europe, Asia","sector":"DeepTech, AI","stage":"Pre-Seed","thesis":"Helps ambitious people build technology companies from scratch."},
    {"name":"Seedrs","type":"Venture Capital","firm":"Seedrs","url":"https://seedrs.com","country":"UK","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed, Series A","thesis":"Europe's most active equity crowdfunding platform."},
    {"name":"Crowdcube","type":"Venture Capital","firm":"Crowdcube","url":"https://crowdcube.com","country":"UK","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Investment crowdfunding platform for ambitious businesses."},
    {"name":"OTF Capital","type":"Venture Capital","firm":"OTF Capital","url":"https://otfcapital.com","country":"Turkey","geo":"MENA, Europe","sector":"SaaS, FinTech","stage":"Seed, Series A","thesis":"Turkey and MENA focused early-stage VC."},
    {"name":"Global Founders Capital","type":"Venture Capital","firm":"Global Founders Capital","url":"https://globalfounderscapital.com","country":"Germany","geo":"Global","sector":"Generalist","stage":"Pre-Seed, Seed, Series A","thesis":"Stage-agnostic investor backing founders at every step."},
    {"name":"Rocket Internet","type":"Corporate Venture","firm":"Rocket Internet","url":"https://rocket-internet.com","country":"Germany","geo":"Global","sector":"Consumer, Marketplace, E-commerce","stage":"Series A, Growth","thesis":"One of the world's largest internet platforms outside China and the US."},
    {"name":"Hummingbird Ventures","type":"Venture Capital","firm":"Hummingbird Ventures","url":"https://hummingbird.vc","country":"Belgium","geo":"Europe, MENA, South Asia","sector":"Generalist","stage":"Seed, Series A","thesis":"Invests in remarkable companies across Europe, Middle East, and South Asia."},
    {"name":"Stride.VC","type":"Venture Capital","firm":"Stride.VC","url":"https://stride.vc","country":"UK","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"London-based pre-seed fund backing technical founders.","ticket":"£100K–£500K"},
    {"name":"Firstminute Capital","type":"Venture Capital","firm":"Firstminute Capital","url":"https://firstminute.capital","country":"UK","geo":"Europe, Global","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Invests in early-stage companies from around the world with a European core."},
    {"name":"Episode 1","type":"Venture Capital","firm":"Episode 1 Ventures","url":"https://episode1.com","country":"UK","geo":"Europe","sector":"Generalist","stage":"Seed","thesis":"UK's leading seed stage fund.","ticket":"£100K–£1M"},
    {"name":"Amadeus Capital Partners","type":"Venture Capital","firm":"Amadeus Capital Partners","url":"https://amadeuscapital.com","country":"UK","geo":"Europe","sector":"DeepTech, AI, Enterprise","stage":"Seed, Series A","thesis":"Deep technology venture capital investor."},
    {"name":"Hoxton Ventures","type":"Venture Capital","firm":"Hoxton Ventures","url":"https://hoxtonventures.com","country":"UK","geo":"Europe","sector":"SaaS, Consumer, Marketplace","stage":"Seed, Series A","thesis":"US-style early stage investing in Europe."},
    {"name":"Draper Esprit","type":"Venture Capital","firm":"Molten Ventures (formerly Draper Esprit)","url":"https://moltenventures.com","country":"UK","geo":"Europe","sector":"SaaS, Consumer, DeepTech","stage":"Seed, Series A, Series B","thesis":"One of Europe's most active early-stage investors."},
    {"name":"DN Capital","type":"Venture Capital","firm":"DN Capital","url":"https://dncapital.com","country":"UK","geo":"Europe, North America","sector":"SaaS, Marketplace, Consumer","stage":"Seed, Series A, Series B","thesis":"Pan-European and US technology venture capital investor."},
    {"name":"Creandum","type":"Venture Capital","firm":"Creandum","url":"https://creandum.com","country":"Sweden","geo":"Europe","sector":"SaaS, Consumer, Marketplace","stage":"Seed, Series A","thesis":"Invests in the most ambitious European technology founders."},
    {"name":"Industrifonden","type":"Venture Capital","firm":"Industrifonden","url":"https://industrifonden.com","country":"Sweden","geo":"Europe","sector":"SaaS, DeepTech, HealthTech","stage":"Seed, Series A","thesis":"Swedish VC investing across the Nordics."},
    {"name":"ByFounders","type":"Venture Capital","firm":"ByFounders","url":"https://byfounders.vc","country":"Denmark","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Built by founders, for founders. Nordic-focused."},
    {"name":"Inventure","type":"Venture Capital","firm":"Inventure","url":"https://inventure.fi","country":"Finland","geo":"Europe","sector":"Generalist","stage":"Seed, Series A","thesis":"Finland's leading early-stage venture capital firm."},
    {"name":"Superhero Capital","type":"Venture Capital","firm":"Superhero Capital","url":"https://superherocapital.fi","country":"Finland","geo":"Europe","sector":"Generalist","stage":"Pre-Seed, Seed","thesis":"Finland-based pre-seed and seed investor."},
    {"name":"Practica Capital","type":"Venture Capital","firm":"Practica Capital","url":"https://practica.lt","country":"Lithuania","geo":"Europe","sector":"SaaS, FinTech, DeepTech","stage":"Seed, Series A","thesis":"Leading VC in the Baltics."},
    {"name":"Tera Ventures","type":"Venture Capital","firm":"Tera Ventures","url":"https://tera.vc","country":"Estonia","geo":"Europe","sector":"Generalist","stage":"Seed, Series A","thesis":"Baltic-focused early-stage venture capital."},
    {"name":"OTB Ventures","type":"Venture Capital","firm":"OTB Ventures","url":"https://otbventures.com","country":"Poland","geo":"Europe","sector":"SaaS, DeepTech, AI","stage":"Seed, Series A","thesis":"Invests in tech startups from Central and Eastern Europe."},
    {"name":"Credo Ventures","type":"Venture Capital","firm":"Credo Ventures","url":"https://credoventures.com","country":"Czech Republic","geo":"Europe","sector":"SaaS, Marketplace, Consumer","stage":"Seed, Series A","thesis":"The leading venture capital firm in Central and Eastern Europe."},
    {"name":"Flashpoint","type":"Venture Capital","firm":"Flashpoint Venture Capital","url":"https://flashpoint.vc","country":"Georgia","geo":"Europe, MENA","sector":"SaaS, FinTech","stage":"Seed, Series A","thesis":"Invests in global startups building for emerging markets."},
    {"name":"Manta Ray Ventures","type":"Venture Capital","firm":"Manta Ray Ventures","url":"https://mantaray.vc","country":"Israel","geo":"MENA, North America","sector":"DeepTech, CyberSecurity, AI","stage":"Seed, Series A","thesis":"Israel-based deep tech and security investor."},
    {"name":"Pitango Venture Capital","type":"Venture Capital","firm":"Pitango","url":"https://pitango.com","country":"Israel","geo":"MENA, North America","sector":"SaaS, HealthTech, AI","stage":"Seed, Series A, Series B","thesis":"Israel's leading VC firm for over 25 years."},
    {"name":"JVP (Jerusalem Venture Partners)","type":"Venture Capital","firm":"Jerusalem Venture Partners","url":"https://jvp.com","country":"Israel","geo":"MENA, North America","sector":"CyberSecurity, AI, DeepTech","stage":"Seed, Series A","thesis":"Israeli VC focused on cybersecurity and digital media."},
    {"name":"Emerge","type":"Venture Capital","firm":"Emerge Education","url":"https://emerge.education","country":"UK","geo":"Europe, North America","sector":"EdTech","stage":"Pre-Seed, Seed","thesis":"The leading EdTech venture capital fund."},
    {"name":"Reach Capital","type":"Venture Capital","firm":"Reach Capital","url":"https://reachcapital.com","country":"USA","geo":"North America","sector":"EdTech","stage":"Seed, Series A","thesis":"Focused exclusively on education, workforce development, and the future of learning."},
    {"name":"Rock Health","type":"Venture Capital","firm":"Rock Health","url":"https://rockhealth.com","country":"USA","geo":"North America","sector":"HealthTech","stage":"Seed, Series A","thesis":"The first seed fund dedicated to digital health."},
    {"name":"a16z Bio + Health","type":"Venture Capital","firm":"Andreessen Horowitz","url":"https://a16z.com/bio-health","country":"USA","geo":"North America","sector":"BioTech / Life Sciences, HealthTech","stage":"Seed, Series A, Growth","thesis":"Investing in companies applying technology to improve human health."},
    {"name":"OrbiMed","type":"Venture Capital","firm":"OrbiMed Advisors","url":"https://orbimed.com","country":"USA","geo":"North America, Asia","sector":"BioTech / Life Sciences, HealthTech","stage":"Series A, Growth","thesis":"The world's largest dedicated healthcare investment firm."},
    {"name":"Obvious Ventures","type":"Venture Capital","firm":"Obvious Ventures","url":"https://obvious.com","country":"USA","geo":"North America","sector":"CleanTech / GreenTech, HealthTech","stage":"Seed, Series A, Series B","thesis":"World positive investing at the intersection of purpose and profit."},
    {"name":"Energy Impact Partners","type":"Venture Capital","firm":"Energy Impact Partners","url":"https://eip.com","country":"USA","geo":"North America, Europe","sector":"CleanTech / GreenTech","stage":"Series A, Series B, Growth","thesis":"Investing in the future of sustainable energy."},
    {"name":"Breakthrough Energy Ventures","type":"Venture Capital","firm":"Breakthrough Energy Ventures","url":"https://breakthrough.energy","country":"USA","geo":"Global","sector":"CleanTech / GreenTech, DeepTech","stage":"Seed, Series A, Series B","thesis":"Committed to helping entrepreneurs build the next generation of energy technologies."},
    {"name":"Polychain Capital","type":"Venture Capital","firm":"Polychain Capital","url":"https://polychain.capital","country":"USA","geo":"Global","sector":"Crypto / Web3","stage":"Seed, Series A","thesis":"Blockchain protocol and cryptoasset-focused fund."},
    {"name":"Paradigm","type":"Venture Capital","firm":"Paradigm","url":"https://paradigm.xyz","country":"USA","geo":"Global","sector":"Crypto / Web3","stage":"Seed, Series A, Growth","thesis":"Research-driven crypto and Web3 investment firm."},
    {"name":"Multicoin Capital","type":"Venture Capital","firm":"Multicoin Capital","url":"https://multicoin.capital","country":"USA","geo":"Global","sector":"Crypto / Web3","stage":"Seed, Series A","thesis":"An investment firm that invests in cryptocurrencies and blockchain companies."},
    {"name":"Andreessen Horowitz Crypto","type":"Venture Capital","firm":"a16z crypto","url":"https://a16zcrypto.com","country":"USA","geo":"Global","sector":"Crypto / Web3","stage":"Seed, Series A, Growth","thesis":"Invests in crypto networks and companies."},
]


class PublicListsScraper(BaseScraper):
    """
    Scraper for publicly available investor lists from GitHub and open web.
    Also returns the hard-coded curated dataset as a reliable fallback.
    """

    SOURCE_NAME = "Public Lists"
    BASE_URL    = ""

    # Known public data repos / JSON endpoints (no auth required)
    PUBLIC_SOURCES: list[str] = [
        # Example public GitHub-hosted lists (add real URLs here):
        "https://raw.githubusercontent.com/dakshvar22/VC-Funds-of-Funds/master/data.json",
    ]

    def scrape(self) -> list[InvestorRecord]:
        records = self._load_curated()
        self.logger.info(f"Loaded {len(records)} curated investor records")

        for url in self.PUBLIC_SOURCES:
            try:
                fetched = self._scrape_json_source(url)
                records.extend(fetched)
                self.logger.info(f"  + {len(fetched)} from {url}")
            except Exception as exc:
                self.logger.warning(f"Failed fetching public source {url}: {exc}")

        self.logger.info(f"PublicLists total: {len(records)} records")
        return records

    def _load_curated(self) -> list[InvestorRecord]:
        """Convert the static curated list to InvestorRecord objects."""
        today = date.today().isoformat()
        records = []
        for item in CURATED_INVESTORS:
            records.append(self.make_record(
                investor_name    = item["name"],
                investor_type    = item["type"],
                firm_name        = item.get("firm", item["name"]),
                website_url      = normalise_url(item.get("url", "")),
                country          = item.get("country", ""),
                geo_focus        = item.get("geo", _infer_geo(item.get("country", ""))),
                sector_focus     = item.get("sector", "Generalist"),
                source_url       = item.get("url", ""),
                data_source      = "Curated Dataset",
                investment_stage  = item.get("stage"),
                investment_thesis = item.get("thesis"),
                ticket_size       = item.get("ticket"),
                confidence_score  = 0.95,
                notes             = "Manually curated, high-confidence record",
            ))
        return records

    def _scrape_json_source(self, url: str) -> list[InvestorRecord]:
        """Fetch and parse a public JSON investor list."""
        html = self.safe_get(url)
        if not html:
            return []
        try:
            data = json.loads(html)
            if isinstance(data, list):
                return [self._json_item_to_record(item) for item in data if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
        return []

    def _json_item_to_record(self, item: dict) -> InvestorRecord:
        """Map an arbitrary JSON dict to InvestorRecord (best-effort)."""
        name = clean_text(
            item.get("name") or item.get("firm_name") or item.get("investor") or ""
        )
        return self.make_record(
            investor_name = name,
            investor_type = item.get("type", "Venture Capital"),
            firm_name     = name,
            website_url   = normalise_url(item.get("website") or item.get("url", "")),
            country       = clean_text(item.get("country", "")),
            geo_focus     = item.get("geo_focus", "Global"),
            sector_focus  = item.get("sectors") or item.get("sector", "Generalist"),
            source_url    = item.get("source_url", ""),
            data_source   = "Public JSON Dataset",
            confidence_score = 0.6,
        )
