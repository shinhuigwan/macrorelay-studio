const tokenInput = document.getElementById("token");
const status = document.getElementById("status");
chrome.storage.local.get("token").then(({token}) => { tokenInput.value = token || ""; });
document.getElementById("save").addEventListener("click", async () => {
  const token = tokenInput.value.trim();
  if (token.length < 20) { status.textContent = "덱덱에서 발급한 연결 코드를 입력해 주세요."; return; }
  await chrome.storage.local.set({token});
  status.textContent = "저장했습니다. Whale에서 탭을 전환하면 덱덱에 전달됩니다.";
});
