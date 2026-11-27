const activityElement = document.getElementById("activity-data");
if (activityElement) {
  const activity = JSON.parse(activityElement.textContent);
  const grid = document.querySelector(".activity-grid");
  if (grid) {
    grid.className = "activity-grid rendered";
    const today = new Date();
    for (let i = 29; i >= 0; i -= 1) {
      const date = new Date(today);
      date.setDate(today.getDate() - i);
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      const count = activity[key] || 0;
      const cell = document.createElement("span");
      cell.title = `${key}: ${count}件`;
      cell.dataset.level = count >= 4 ? "4" : count >= 3 ? "3" : count >= 2 ? "2" : count >= 1 ? "1" : "0";
      grid.appendChild(cell);
    }
  }
}
