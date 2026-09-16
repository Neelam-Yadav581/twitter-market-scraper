"""CSS/XPath selectors for X (Twitter) search result pages.

Kept in one place because X changes its DOM often; when scraping breaks,
update the selectors here instead of hunting through the scraper logic.
"""

SEARCH_URL_TEMPLATE = "https://x.com/search?q={query}&src=typed_query&f=live"

TWEET_ARTICLE = "article[data-testid='tweet']"
TWEET_TEXT = "[data-testid='tweetText']"
TWEET_TIME = "time"
TWEET_USER_NAME = "[data-testid='User-Name']"
TWEET_REPLY_COUNT = "[data-testid='reply']"
TWEET_RETWEET_COUNT = "[data-testid='retweet']"
TWEET_LIKE_COUNT = "[data-testid='like']"
TWEET_VIEW_COUNT = "a[href$='/analytics'] span"

# Present on every logged-in page load regardless of whether the home feed
# has any tweets to show yet - a more reliable "am I logged in" signal than
# waiting for a tweet article, which can false-negative on a slow first
# render or a near-empty feed.
LOGGED_IN_MARKER_CANDIDATES = [
    "[data-testid='SideNav_AccountSwitcher_Button']",
    "[data-testid='AppTabBar_Home_Link']",
    "[data-testid='primaryColumn']",
]

RATE_LIMIT_MARKERS = [
    "Something went wrong",
    "Try again",
    "unusual activity",
    "verify your identity",
    "Rate limit exceeded",
]
