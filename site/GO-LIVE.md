# Putting the Freighter page online (about 10 minutes, free)

Netlify hosts the page and collects sign-ups. Free plan: 100 sign-ups a month, which is plenty for a beta list.

## 1. Create the site

1. Make a free account at https://app.netlify.com/signup (signing up with GitHub or email both work).
2. Go to **Sites → Add new site → Deploy manually** (or open https://app.netlify.com/drop).
3. Drag the **`deploy`** folder (inside `site/`) onto the page. In a few seconds you get a link like
   `https://random-name-123.netlify.app`.
4. **Site configuration → Change site name** → `freighter-studio` (plain `freighter` is already taken by someone else) → your page is at
   `https://freighter-studio.netlify.app`.

## 2. Turn on sign-ups

New Netlify sites have form collection switched off.

1. **Site configuration → Forms → Enable form detection.**
2. Deploy again so Netlify sees the form: **Deploys** tab → drag the `deploy` folder onto the
   "Need to update your site?" box.
3. Check: **Forms** now lists a form called **waitlist**.

## 3. Test it

1. Open your live page and sign up with your own email.
2. In Netlify, **Forms → waitlist** should show the entry with all answers.
3. Get an email for each sign-up: **Forms → Form notifications → Add notification → Email notification**.
4. Delete your test entry afterwards so your numbers stay clean.

## 4. Read your results

**Forms → waitlist → Download as CSV.** The form asks two things, so two columns matter for the demand test:
- `email`: how many people signed up, and who to write to when the public beta opens (issue #49)
- `daw`: which DAWs people use (FL Studio, Ableton Live, Other; one person can tick several). Count each one:
  the FL Studio and Ableton split says which to test hardest, and lots of "Other" makes the case for more DAWs
  after 1.0 (issue #54)

Rule of thumb: **200+ emails** means there's real demand for a 1.0.

## When someone asks to come off the list

The page promises that replying to any email takes people off the list (Netlify Forms has no unsubscribe link).
When someone replies asking: **Forms → waitlist**, find their email, open the entry and **Delete** it. Delete it from
any copy you downloaded as CSV too.

## Changing the page later

Edit `landing.html` (or ask Claude), run `python3 build.py`, then drag the `deploy` folder onto the
Deploys tab again. Sign-ups already collected stay put.

## Later: your own domain

Buy a domain (about $10–15/year, e.g. at Namecheap or Cloudflare), then in Netlify:
**Domain management → Add a domain**, and follow its steps. The names are picked (the product is Freighter, the
company Samplistics); check them and choose the domain in issue #47 before you buy.
