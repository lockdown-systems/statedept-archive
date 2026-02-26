document.addEventListener("DOMContentLoaded", function() {
  if (typeof YEAR_MONTH === "undefined") return;
  const listEl = document.getElementById("tweet-list");
  const loadMoreBtn = document.getElementById("load-more");
  const doneMsg = document.getElementById("done-msg");
  if (!listEl) return;

  fetch("../data/" + YEAR_MONTH + ".json")
    .then(function(r) { return r.json(); })
    .then(function(data) {
      const tweets = data.tweets || [];
      let shown = 0;
      const pageSize = typeof PAGE_SIZE !== "undefined" ? PAGE_SIZE : 50;

      function renderTweet(t) {
        const card = document.createElement("div");
        card.className = "tweet-card";
        const meta = document.createElement("p");
        meta.className = "tweet-meta";
        meta.textContent = t.created_at;
        card.appendChild(meta);
        const text = document.createElement("div");
        text.className = "tweet-text" + (t.text.length > 200 ? " truncated" : "");
        text.textContent = t.text.length > 200 ? t.text.slice(0, 200) + "…" : t.text;
        card.appendChild(text);
        const link = document.createElement("a");
        link.href = "../tweet/" + t.id + ".html";
        link.textContent = "View tweet" + (t.media_count > 0 ? " (" + t.media_count + " media)" : "");
        link.style.marginTop = "0.5rem";
        link.style.display = "inline-block";
        card.appendChild(link);
        return card;
      }

      function showNext() {
        const end = Math.min(shown + pageSize, tweets.length);
        for (let i = shown; i < end; i++) {
          listEl.appendChild(renderTweet(tweets[i]));
        }
        shown = end;
        if (shown >= tweets.length) {
          loadMoreBtn.style.display = "none";
          doneMsg.style.display = "block";
          doneMsg.textContent = "All " + tweets.length + " tweets shown.";
        } else {
          loadMoreBtn.style.display = "block";
          loadMoreBtn.textContent = "Load more (" + (tweets.length - shown) + " left)";
        }
      }

      loadMoreBtn.addEventListener("click", function() { showNext(); });
      showNext();
    })
    .catch(function(e) {
      listEl.innerHTML = "<p>Failed to load month data.</p>";
      console.error(e);
    });
});
