var BOT_WEBHOOK_URL = "https://lagging-prevail-cure.ngrok-free.dev/webhook/sheet_status";
var WEBHOOK_SECRET = "ca5xjwy5ex4QahythOagtse0lpoI94RtYEJA2CWBDRtO97rGF2txPlA403vE8qpS";

// 1. ID таблицы-шаблона отчётника
var TEMPLATE_SHEET_ID = "1FagJffQuPtDlvN0NkRlNwRgkNlFC9bOBHR1LoyOt1-c";

// 2. Email сервисного аккаунта бота для автоматической выдачи прав на чтение
var SERVICE_ACCOUNT_EMAIL = "crm-bot@crm-bot-507115.iam.gserviceaccount.com";

var NOTIFY_COLUMNS = [
  "статус",
  "подтвержд",
  "прод",
  "смена",
  "отчетник",
  "причин",
  "собес"
];

var lastCreateError = "";

function isInterviewScheduleHeader(header) {
  return /^дата и время собес(едования|а)$/.test(String(header || "").trim().toLowerCase());
}

function formatInterviewSchedule(data) {
  var date = String(data.date || "").trim();
  var time = String(data.time || "").trim();
  return [date, time ? time + " МСК" : ""].filter(function (part) { return part; }).join(" ");
}

function ensureInterviewScheduleColumn(sheet) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var headerRow = findHeaderRow(sheet);
    var headers = sheet.getRange(headerRow, 1, 1, Math.max(sheet.getLastColumn(), 1)).getValues()[0];
    var idColumn = -1, scheduleColumn = -1;
    for (var i = 0; i < headers.length; i++) {
      var header = String(headers[i] || "").trim().toLowerCase();
      if (["id", "id заявки", "№", "№ заявки"].indexOf(header) !== -1) idColumn = i + 1;
      if (isInterviewScheduleHeader(header)) scheduleColumn = i + 1;
    }
    if (idColumn === -1) throw new Error("В листе заявок не найден столбец ID");
    if (scheduleColumn === -1) {
      sheet.insertColumnAfter(idColumn);
      scheduleColumn = idColumn + 1;
      sheet.getRange(headerRow, scheduleColumn).setValue("Дата и время собеседования");
      sheet.setColumnWidth(scheduleColumn, 220);
    } else if (scheduleColumn !== idColumn + 1) {
      sheet.moveColumns(sheet.getRange(headerRow, scheduleColumn), idColumn + 1);
    }
    SpreadsheetApp.flush();
    return headerRow;
  } finally {
    lock.releaseLock();
  }
}

function setupApplicationsSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ensureInterviewScheduleColumn(ss.getSheetByName("Заявки") || ss.getSheets()[0]);
}

function applyPartnerAcceptanceValidation(sheet, row, column, headerRow) {
  var target = sheet.getRange(row, column);
  if (row > headerRow + 1) {
    try {
      var source = sheet.getRange(row - 1, column);
      if (source.getDataValidation && source.getDataValidation()) {
        source.copyTo(target, SpreadsheetApp.CopyPasteType.PASTE_DATA_VALIDATION, false);
        return;
      }
    } catch (eValidationCopy) { }
  }

  var acceptanceRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(["Принято", "Не принято"], true)
    .setAllowInvalid(false)
    .build();
  target.setDataValidation(acceptanceRule);
}

function findPartnerAcceptanceColumn(headers) {
  var statusColumn = -1;
  for (var i = 0; i < headers.length; i++) {
    var header = String(headers[i] || "").trim().toLowerCase().replace(/ё/g, "е");
    if (header.indexOf("принят") !== -1 && header.indexOf("агент") === -1 &&
      header.indexOf("причин") === -1 && header.indexOf("дата") === -1) {
      return i + 1;
    }
    if (header === "статус заявки") return i + 1;
    if (header === "статус" && statusColumn === -1) statusColumn = i + 1;
  }
  return statusColumn;
}

function doPost(e) {
  try {
    lastCreateError = "";
    var contents = e.postData.contents;
    var data = JSON.parse(contents);
    var ss = SpreadsheetApp.getActiveSpreadsheet();

    // 📌 Обновление столбца "Подтверждение агента" от бота (во ВСЕХ листах)
    if (data.action === "update_agent_confirmation") {
      var allSheets = [ss.getSheetByName("Заявки"), ss.getSheetByName("Запуски")].filter(function (sh) {
        return sh !== null;
      });
      var updatedSheets = [];

      for (var s = 0; s < allSheets.length; s++) {
        var sh = allSheets[s];
        var headerRowIndex = findHeaderRow(sh);
        var lastCol = sh.getLastColumn();
        if (lastCol < 1) continue;

        var headers = sh.getRange(headerRowIndex, 1, 1, lastCol).getValues()[0];
        var idCol = -1, confCol = -1, modelNameCol = -1;

        for (var c = 0; c < headers.length; c++) {
          var hName = String(headers[c]).trim().toLowerCase();
          if ((hName === "id" || hName === "id заявки" || hName === "№" || hName === "№ заявки") && hName.indexOf("агент") === -1 && hName.indexOf("agent") === -1) idCol = c + 1;
          if (hName.indexOf("подтвержд") !== -1 || hName.indexOf("подтверждение") !== -1) confCol = c + 1;
          if (hName === "фио" || hName === "фио модели") modelNameCol = c + 1;
        }

        if (confCol !== -1) {
          var lastRow = sh.getLastRow();
          for (var r = headerRowIndex + 1; r <= lastRow; r++) {
            var rowId = idCol !== -1 ? String(sh.getRange(r, idCol).getValue()).trim() : "";
            var targetId = data.model_code ? String(data.model_code).trim() : "";

            var isMatch = false;
            // 🎯 Единственный способ сопоставления — точный ID модели.
            if (targetId) {
              if (rowId && normalizeModelCode(rowId) === normalizeModelCode(targetId)) {
                isMatch = true;
              }
            }

            // Для старых строк без ID модели используем точное совпадение ФИО.
            if (!isMatch && !rowId && modelNameCol !== -1 && data.model_name) {
              var rowModelName = String(sh.getRange(r, modelNameCol).getValue()).trim();
              isMatch = rowModelName === String(data.model_name).trim();
            }

            if (isMatch) {
              if (idCol !== -1 && data.model_code) sh.getRange(r, idCol).setValue(data.model_code);
              sh.getRange(r, confCol).setValue(data.status || "✅ Подтверждено");
              if (modelNameCol !== -1 && data.model_name) sh.getRange(r, modelNameCol).setValue(data.model_name);
              updatedSheets.push(sh.getName() + " (строка " + r + ")");
            }
          }
        }
      }

      if (updatedSheets.length > 0) {
        return ContentService.createTextOutput(JSON.stringify({ status: "success", updated: updatedSheets.join(", ") }))
          .setMimeType(ContentService.MimeType.JSON);
      }
      return ContentService.createTextOutput(JSON.stringify({ status: "not_found" }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    // 📌 Создание новой заявки на Листе 1
    var sheet = ss.getSheetByName("Заявки") || ss.getSheets()[0];

    var headerRowIndex = ensureInterviewScheduleColumn(sheet);
    var lastCol = Math.max(sheet.getLastColumn(), 12);
    var headers = sheet.getRange(headerRowIndex, 1, 1, lastCol).getValues()[0];
    var acceptanceColumn = findPartnerAcceptanceColumn(headers);

    var newRow = new Array(headers.length).fill("");
    var modelName = (data.model_name && String(data.model_name).trim().length > 0) ? String(data.model_name).trim() : extractModelName(data.form);
    var modelTg = (data.model_tg && String(data.model_tg).trim().length > 0) ? String(data.model_tg).trim() : extractTgUsername(data.form);
    var modelPhone = (data.model_phone && String(data.model_phone).trim().length > 0) ? String(data.model_phone).trim() : extractModelPhone(data.form);
    var modelCode = data.model_code ? String(data.model_code).trim() : "";

    // 🪄 Авто-создание персонального отчётника модели
    var createdReportUrl = createReportSheetForModel(modelName, modelTg, ss);

    for (var i = 0; i < headers.length; i++) {
      var h = String(headers[i]).trim().toLowerCase();

      if (isInterviewScheduleHeader(h)) {
        newRow[i] = formatInterviewSchedule(data);
      } else if (h === "фио" || h === "фио модели") {
        newRow[i] = modelName;
      } else if (h === "отчетник") {
        newRow[i] = createdReportUrl;
      } else if (h === "tg" || h === "телеграм" || h === "tg модели") {
        newRow[i] = modelTg;
      } else if (h === "телефон" || h === "номер" || h === "телефон модели") {
        newRow[i] = modelPhone;
      } else if (h === "id" || h === "id заявки" || h === "№" || h === "№ заявки") {
        newRow[i] = data.model_code || "";
      } else if (h === "id агента" || h === "айди агента" || h === "агент id" || h === "номер агента" || (h.indexOf("агент") !== -1 && h.indexOf("подтвержд") === -1)) {
        newRow[i] = data.agent_id || data.agent_code || "";
      } else {
        newRow[i] = "";
      }
    }

    if (acceptanceColumn !== -1) newRow[acceptanceColumn - 1] = "Принято";
    sheet.appendRow(newRow);
    var insertedRow = sheet.getLastRow();

    // 🎨 Сохранение форматирования (шрифта, размера, выравнивания) с предыдущей строки
    if (insertedRow > headerRowIndex + 1) {
      try {
        var prevRowRange = sheet.getRange(insertedRow - 1, 1, 1, lastCol);
        var newRowRange = sheet.getRange(insertedRow, 1, 1, lastCol);
        prevRowRange.copyTo(newRowRange, SpreadsheetApp.CopyPasteType.PASTE_FORMAT, false);
      } catch (eFmt) { }
    }

    if (acceptanceColumn !== -1) applyPartnerAcceptanceValidation(sheet, insertedRow, acceptanceColumn, headerRowIndex);

    // Если создан отчётник — вставляем в столбец "Отчетник" красивую ссылку-чип =HYPERLINK()
    if (createdReportUrl) {
      for (var k = 0; k < headers.length; k++) {
        var hName = String(headers[k]).trim().toLowerCase();
        if (hName === "отчетник") {
          var prettyFormula = '=HYPERLINK("' + createdReportUrl + '", "📗 Отчётник — ' + modelName + '")';
          sheet.getRange(insertedRow, k + 1).setFormula(prettyFormula);
          break;
        }
      }
      sendReportUrlToBot(data.model_code, modelName, createdReportUrl);
    }

    if (acceptanceColumn !== -1) {
      // Изменения из скрипта не вызывают onEdit: синхронизируем статус и кнопки в боте явно.
      onEditHandler({range: sheet.getRange(insertedRow, acceptanceColumn), value: "Принято", oldValue: ""});
    }

    return ContentService.createTextOutput(JSON.stringify({
      status: "success",
      row: insertedRow,
      report_url: createdReportUrl,
      error_detail: lastCreateError
    })).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function onEditHandler(e) {
  if (!e || !e.range) return;

  var sheet = e.range.getSheet();
  var row = e.range.getRow();
  var editedCol = e.range.getColumn();

  var headerRowIndex = findHeaderRow(sheet);
  if (row <= headerRowIndex) return;

  var headers = sheet.getRange(headerRowIndex, 1, 1, sheet.getLastColumn()).getValues()[0];
  var editedHeaderName = String(headers[editedCol - 1] || "").trim().toLowerCase();

  var isSheet2 = sheet.getName().toLowerCase().indexOf("запуск") !== -1;
  var isMainSheet = ["заявки", "запуски"].indexOf(sheet.getName().toLowerCase().trim()) !== -1;
  var acceptanceColumn = isSheet2 ? -1 : findPartnerAcceptanceColumn(headers);
  var isPartnerAcceptance = editedCol === acceptanceColumn;

  // Дата приходит из записи в боте и не является этапом согласования.
  if (isInterviewScheduleHeader(editedHeaderName)) {
    e.range.setValue(e.oldValue !== undefined ? e.oldValue : "");
    SpreadsheetApp.getActiveSpreadsheet().toast("Дата и время заполняются из бота", "Защита данных", 3);
    return;
  }

  // Финансовые колонки заполняются только ботом, партнёрам их менять нельзя.
  var isFinancialColumn = editedHeaderName.indexOf("реф") !== -1 ||
    editedHeaderName.indexOf("выплат") !== -1;
  if (isFinancialColumn) {
    e.range.setValue(e.oldValue !== undefined ? e.oldValue : "");
    SpreadsheetApp.getActiveSpreadsheet().toast(
      "⚠️ Реф и выплата заполняются автоматически", "Защита данных", 4
    );
    return;
  }

  var isAllowedEdit = isPartnerAcceptance || NOTIFY_COLUMNS.some(function (col) {
    return editedHeaderName.indexOf(col) !== -1;
  }) || (isSheet2 && (editedHeaderName.indexOf("фио") !== -1 || editedHeaderName.indexOf("модель") !== -1 || editedHeaderName.indexOf("имя") !== -1));

  // 🛡 Защита не-статусных столбцов от случайных изменений партнёром
  if (!isAllowedEdit && e.oldValue !== undefined) {
    e.range.setValue(e.oldValue);
    SpreadsheetApp.getActiveSpreadsheet().toast("⚠️ Редактирование этой ячейки ограничено", "Защита данных", 3);
    return;
  }

  var isWatched = isPartnerAcceptance || NOTIFY_COLUMNS.some(function (col) {
    return editedHeaderName.indexOf(col) !== -1;
  });

  // Любое изменение в основной таблице пишем в историю, но уведомляем бота
  // только по колонкам, которые влияют на рабочий процесс.
  if (!isWatched && !isMainSheet) return;

  var modelName = "";
  var modelCode = "";
  var statusReason = "";
  var currentStatus = "";

  for (var i = 0; i < headers.length; i++) {
    var h = String(headers[i]).trim().toLowerCase();
    if ((h === "id" || h === "id заявки" || h === "№" || h === "№ заявки") && h.indexOf("агент") === -1 && h.indexOf("agent") === -1) {
      modelCode = normalizeModelCode(sheet.getRange(row, i + 1).getValue());
    }
    if (h.indexOf("id модели") !== -1 || h.indexOf("айди модели") !== -1 || h.indexOf("спец-номер") !== -1) {
      modelCode = normalizeModelCode(sheet.getRange(row, i + 1).getValue());
    }
    if (h === "фио" || h === "фио модели" || h.indexOf("фио") !== -1 || (h.indexOf("модель") !== -1 && h.indexOf("телефон") === -1 && h.indexOf("id") === -1 && h.indexOf("айди") === -1 && h.indexOf("спец") === -1)) {
      modelName = String(sheet.getRange(row, i + 1).getValue()).trim();
    }
    if (h.indexOf("причин") !== -1 || h.indexOf("причина") !== -1) {
      statusReason = String(sheet.getRange(row, i + 1).getValue()).trim();
    }
    if (h === "статус" || h.indexOf("статус") !== -1) {
      currentStatus = String(sheet.getRange(row, i + 1).getValue()).trim();
    }
  }
  if (!modelName) modelName = "Модель";

  var oldValue = e.oldValue || "";
  var newValue = e.value || sheet.getRange(row, editedCol).getValue();
  var colTitle = String(headers[editedCol - 1] || "Статус").trim();

  // Статусы можно заполнять только последовательно слева направо.
  var statusColumns = [];
  for (var statusIndex = 0; statusIndex < headers.length; statusIndex++) {
    var statusHeader = String(headers[statusIndex] || "").trim().toLowerCase();
    if (isInterviewScheduleHeader(statusHeader)) continue;
    if (statusIndex + 1 === acceptanceColumn || statusHeader.indexOf("статус") !== -1 || statusHeader.indexOf("собес") !== -1 ||
      statusHeader.indexOf("принят") !== -1 || statusHeader.indexOf("регистрац") !== -1 ||
      statusHeader.indexOf("подтвержд") !== -1 || statusHeader.indexOf("прод") !== -1 ||
      statusHeader.indexOf("созвон") !== -1 || statusHeader.indexOf("смена") !== -1 ||
      statusHeader.indexOf("слив") !== -1 || statusHeader.indexOf("отмен") !== -1) {
      statusColumns.push(statusIndex + 1);
    }
  }
  var editedStatusPosition = statusColumns.indexOf(editedCol);
  if (editedStatusPosition !== -1 && !isSheet2 && !isPartnerAcceptance) {
    var firstEmptyStatusPosition = statusColumns.length;
    for (var statusPosition = 0; statusPosition < statusColumns.length; statusPosition++) {
      if (!String(sheet.getRange(row, statusColumns[statusPosition]).getValue()).trim()) {
        firstEmptyStatusPosition = statusPosition;
        break;
      }
    }

    var isCurrentStatus = editedStatusPosition === firstEmptyStatusPosition;
    var isPreviousStatus = editedStatusPosition === firstEmptyStatusPosition - 1;
    if (!isCurrentStatus && !isPreviousStatus) {
      e.range.setValue(e.oldValue !== undefined ? e.oldValue : "");
      SpreadsheetApp.getActiveSpreadsheet().toast(
        "⚠️ Можно менять только текущий и предыдущий статусы", "Последовательность статусов", 4
      );
      return;
    }
  }

  if (colTitle.toLowerCase().indexOf("причин") !== -1) {
    statusReason = String(newValue).trim();
    if (currentStatus) {
      newValue = currentStatus;
    }
  }

  var isReportColumn = editedHeaderName.indexOf("отчетник") !== -1;
  var userEmail = Session.getActiveUser().getEmail() || "";
  var isModelNameEdit = isSheet2 && (editedHeaderName.indexOf("фио") !== -1 || editedHeaderName.indexOf("имя") !== -1);

  if (isSheet2 && modelCode) {
    var firstLaunchRow = findRowByModelCode(sheet, headerRowIndex, modelCode);
    if (firstLaunchRow > headerRowIndex) {
      removeDuplicateRowsByModelCode(sheet, headerRowIndex, modelCode, firstLaunchRow);
    }
  }

  if (isModelNameEdit && modelCode) {
    var reportUrlForRename = "";
    for (var reportIndex = 0; reportIndex < headers.length; reportIndex++) {
      if (String(headers[reportIndex]).trim().toLowerCase().indexOf("отчетник") !== -1) {
        reportUrlForRename = extractUrlFromCell(sheet.getRange(row, reportIndex + 1));
        break;
      }
    }
    if (reportUrlForRename) updateReportModelName(reportUrlForRename, modelName);
  }

  // 🔄 Если выставлен статус "Регистрация" — автоматически переносим модель на Лист 2 ("Запуски")
  if (String(newValue).trim().toLowerCase() === "регистрация") {
    try {
      var ss = SpreadsheetApp.getActiveSpreadsheet();
      var targetSheet = ss.getSheetByName("Запуски");
      var defaultHeaders = ["ID", "ФИО", "Отчетник", "TG", "Телефон", "ID агента", "Причины статуса", "Статус", "Коммент по собесу", "Написал прод", "Созвон с продом", "Коммент по созвону"];

      if (!targetSheet) {
        targetSheet = ss.insertSheet("Запуски");
        targetSheet.appendRow(defaultHeaders);
      }

      var targetHeaderRow = findHeaderRow(targetSheet);
      var targetHeaders = targetSheet.getRange(targetHeaderRow, 1, 1, Math.max(targetSheet.getLastColumn(), 8)).getValues()[0];
      var sourceValues = sheet.getRange(row, 1, 1, headers.length).getValues()[0];

      var modelTg = "";
      var modelPhone = "";
      var agentIdVal = "";
      for (var s = 0; s < headers.length; s++) {
        var shName = String(headers[s]).trim().toLowerCase();
        if (shName === "tg" || shName === "телеграм" || shName === "tg модели") modelTg = String(sourceValues[s]).trim();
        if (shName === "телефон" || shName === "номер" || shName === "телефон модели") modelPhone = String(sourceValues[s]).trim();
        if ((shName === "id агента" || shName === "айди агента" || shName === "агент id" || shName === "номер агента" || shName === "агент") && shName.indexOf("подтвержд") === -1) {
          agentIdVal = String(sourceValues[s]).trim();
        }
      }

      // Создаём отчётник модели для Листа 2
      var reportUrl = createReportSheetForModel(modelName, modelTg, ss);

      var newTargetRow = new Array(targetHeaders.length).fill("");
      for (var t = 0; t < targetHeaders.length; t++) {
        var thName = String(targetHeaders[t]).trim().toLowerCase();

        if (thName === "id" || thName === "id заявки") {
          newTargetRow[t] = modelCode || "";
        } else if (thName.indexOf("фио") !== -1) {
          newTargetRow[t] = modelName;
        } else if (thName === "отчетник") {
          if (reportUrl) {
            newTargetRow[t] = '=HYPERLINK("' + reportUrl + '", "📗 Отчётник — ' + modelName + '")';
          }
        } else if (thName === "tg" || thName === "телеграм" || thName === "tg модели") {
          newTargetRow[t] = modelTg;
        } else if (thName === "телефон" || thName === "номер" || thName === "телефон модели") {
          newTargetRow[t] = modelPhone;
        } else if ((thName === "id агента" || thName === "айди агента" || thName === "агент id" || thName === "номер агента" || thName === "агент") && thName.indexOf("подтвержд") === -1) {
          newTargetRow[t] = agentIdVal;
        }
      }

      var existingTargetRow = findRowByModelCode(targetSheet, targetHeaderRow, modelCode);
      if (existingTargetRow > targetHeaderRow) {
        for (var targetColumn = 0; targetColumn < newTargetRow.length; targetColumn++) {
          if (newTargetRow[targetColumn] !== "") targetSheet.getRange(existingTargetRow, targetColumn + 1).setValue(newTargetRow[targetColumn]);
        }
        removeDuplicateRowsByModelCode(targetSheet, targetHeaderRow, modelCode, existingTargetRow);
      } else {
        targetSheet.appendRow(newTargetRow);
      }

      // Отправляем вебхук в бот с URL отчётника
      if (reportUrl) {
        sendReportUrlToBot(modelCode, modelName, reportUrl);
      }
    } catch (errMove) {
      Logger.log("Ошибка копирования в Запуски: " + errMove.toString());
    }
  }

  if (modelCode || modelName) {
    var payload = {
      action: isModelNameEdit ? "model_name_changed" : "",
      model_code: modelCode || "",
      model_name: modelName || "",
      new_status: colTitle + ": " + newValue,
      col_title: colTitle,
      status_reason: statusReason,
      old_value: String(oldValue).trim(),
      new_value: String(newValue).trim(),
      user_email: userEmail,
      sheet_name: sheet.getName(),
      log_only: !isWatched,
      is_report_column: isReportColumn,
      secret: WEBHOOK_SECRET
    };

    var options = {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    };

    try {
      UrlFetchApp.fetch(BOT_WEBHOOK_URL, options);
    } catch (err) {
      Logger.log("Ошибка отправки вебхука в бот: " + err.toString());
    }
  }
}

function testDrivePermission() {
  var file = SpreadsheetApp.openById(TEMPLATE_SHEET_ID);
  Logger.log("Доступ к шаблону успешно разрешён: " + file.getName());
}

function copyMainSpreadsheetPermissions(mainSs, reportSs) {
  if (!mainSs || !reportSs) return;

  var mainFile, reportFile;
  try {
    mainFile = DriveApp.getFileById(mainSs.getId());
    reportFile = DriveApp.getFileById(reportSs.getId());
  } catch (eFiles) {
    Logger.log("Предупреждение доступа к файлам при копировании прав отчётника: " + eFiles.toString());
    return;
  }

  try {
    reportFile.setSharing(mainFile.getSharingAccess(), mainFile.getSharingPermission());
  } catch (eSharing) {
    Logger.log("Предупреждение копирования общего доступа отчётника: " + eSharing.toString());
  }

  try {
    var editors = mainFile.getEditors();
    for (var i = 0; i < editors.length; i++) {
      var editorEmail = editors[i].getEmail();
      if (editorEmail) reportFile.addEditor(editorEmail);
    }
  } catch (eEditors) {
    Logger.log("Предупреждение копирования редакторов отчётника: " + eEditors.toString());
  }

  try {
    var viewers = mainFile.getViewers();
    for (var j = 0; j < viewers.length; j++) {
      var viewerEmail = viewers[j].getEmail();
      if (viewerEmail) reportFile.addViewer(viewerEmail);
    }
  } catch (eViewers) {
    Logger.log("Предупреждение копирования просмотрщиков отчётника: " + eViewers.toString());
  }
}

/**
 * 🪄 Функция авто-создания отчётника модели через SpreadsheetApp + выдача прав сервисному аккаунту
 */
function createReportSheetForModel(modelName, modelTg, mainSs) {
  if (!TEMPLATE_SHEET_ID || TEMPLATE_SHEET_ID.trim().length < 10) {
    lastCreateError = "TEMPLATE_SHEET_ID пуст или слишком короткий";
    return "";
  }
  try {
    var templateSs = SpreadsheetApp.openById(TEMPLATE_SHEET_ID);
    var newSs = templateSs.copy("Отчётник — " + modelName);
    var newUrl = newSs.getUrl();

    // Отчётник должен быть доступен тем же партнёрам, что и основная таблица.
    copyMainSpreadsheetPermissions(mainSs || SpreadsheetApp.getActiveSpreadsheet(), newSs);

    // 🔑 Автоматически добавляем сервисный аккаунт бота в редакторы таблицы
    if (SERVICE_ACCOUNT_EMAIL && SERVICE_ACCOUNT_EMAIL.trim().length > 5) {
      try {
        newSs.addEditor(SERVICE_ACCOUNT_EMAIL.trim());
      } catch (eView) {
        Logger.log("Предупреждение добавления редактора: " + eView.toString());
      }
    }

    try {
      var mSheet = newSs.getSheets()[0];
      fillModelHeaderInfo(mSheet, modelName, modelTg);
    } catch (eFill) { }

    return newUrl;
  } catch (err) {
    lastCreateError = err.toString();
    Logger.log("ОШИБКА создания отчётника: " + err.toString());
    return "";
  }
}

function fillModelHeaderInfo(sheet, modelName, modelTg) {
  try {
    var maxR = Math.min(sheet.getLastRow(), 10);
    var maxC = Math.min(sheet.getLastColumn(), 15);
    var vals = sheet.getRange(1, 1, maxR, maxC).getValues();

    for (var r = 0; r < vals.length; r++) {
      for (var c = 0; c < vals[r].length; c++) {
        var v = String(vals[r][c]).trim().toLowerCase();
        if (v === "фио") {
          sheet.getRange(r + 2, c + 1).setValue(modelName);
        }
        if (v === "тег" || v === "tg") {
          sheet.getRange(r + 2, c + 1).setValue(modelTg);
        }
      }
    }
  } catch (e) { }
}

function sendReportUrlToBot(modelCode, modelName, reportUrl) {
  var payload = {
    model_code: modelCode || "",
    model_name: modelName || "",
    new_status: "Отчетник: " + reportUrl,
    is_report_column: true,
    secret: WEBHOOK_SECRET
  };

  var options = {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  try {
    UrlFetchApp.fetch(BOT_WEBHOOK_URL, options);
  } catch (err) { }
}

function findHeaderRow(sheet) {
  var maxSearchRows = Math.min(sheet.getLastRow(), 15);
  if (maxSearchRows === 0) return 1;

  var values = sheet.getRange(1, 1, maxSearchRows, Math.max(sheet.getLastColumn(), 10)).getValues();

  for (var r = 0; r < values.length; r++) {
    for (var c = 0; c < values[r].length; c++) {
      var val = String(values[r][c]).trim().toLowerCase();
      if (val === "фио" || val === "фио модели" || val === "tg" || val === "id" || val === "id заявки" || val === "статус") {
        return r + 1;
      }
    }
  }
  return 1;
}

function findRowByModelCode(sheet, headerRowIndex, modelCode) {
  if (!modelCode) return -1;
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(headerRowIndex, 1, 1, lastCol).getValues()[0];
  var idCol = -1;
  for (var c = 0; c < headers.length; c++) {
    var header = String(headers[c]).trim().toLowerCase();
    if (header === "id" || header === "id заявки" || header === "№" || header === "№ заявки") {
      idCol = c + 1;
      break;
    }
  }
  if (idCol === -1) return -1;
  var targetId = String(modelCode).trim();
  for (var row = headerRowIndex + 1; row <= sheet.getLastRow(); row++) {
    if (String(sheet.getRange(row, idCol).getValue()).trim() === targetId) return row;
  }
  return -1;
}

function removeDuplicateRowsByModelCode(sheet, headerRowIndex, modelCode, keepRow) {
  if (!modelCode) return;
  var lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getRange(headerRowIndex, 1, 1, lastCol).getValues()[0];
  var idCol = -1;
  for (var c = 0; c < headers.length; c++) {
    var header = String(headers[c]).trim().toLowerCase();
    if (header === "id" || header === "id заявки" || header === "№" || header === "№ заявки") {
      idCol = c + 1;
      break;
    }
  }
  if (idCol === -1) return;
  var targetId = String(modelCode).trim();
  for (var row = sheet.getLastRow(); row > headerRowIndex; row--) {
    if (row !== keepRow && String(sheet.getRange(row, idCol).getValue()).trim() === targetId) {
      sheet.deleteRow(row);
    }
  }
}

function normalizeModelCode(value) {
  var code = String(value == null ? "" : value).trim();
  return code.replace(/\.0$/, "");
}

function extractUrlFromCell(cell) {
  var formula = cell.getFormula();
  var formulaMatch = formula && formula.match(/HYPERLINK\("([^"]+)"/i);
  if (formulaMatch) return formulaMatch[1];
  var value = String(cell.getValue() || "");
  var valueMatch = value.match(/https?:\/\/docs\.google\.com\/spreadsheets\/d\/[\w-]+/i);
  return valueMatch ? valueMatch[0] : "";
}

function updateReportModelName(reportUrl, modelName) {
  try {
    var reportSs = SpreadsheetApp.openByUrl(reportUrl);
    var sheet = reportSs.getSheets()[0];
    var maxR = Math.min(sheet.getLastRow(), 10);
    var maxC = Math.min(sheet.getLastColumn(), 15);
    var vals = sheet.getRange(1, 1, maxR, maxC).getValues();

    for (var r = 0; r < vals.length; r++) {
      for (var c = 0; c < vals[r].length; c++) {
        var v = String(vals[r][c]).trim().toLowerCase();
        if (v === "фио") {
          sheet.getRange(r + 2, c + 1).setValue(modelName);
        }
      }
    }
  } catch (err) {
    Logger.log("Не удалось обновить ФИО в отчётнике: " + err.toString());
  }
}

function extractTgUsername(formText) {
  if (!formText) return "";
  var match = formText.match(/@[A-Za-z0-9_]{4,}/);
  if (match) return match[0];

  var tmeMatch = formText.match(/t\.me\/([A-Za-z0-9_]{4,})/);
  if (tmeMatch) return "@" + tmeMatch[1];

  var lines = formText.split("\n");
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    if (line.toLowerCase().indexOf("телеграм") !== -1 || line.indexOf("4)") !== -1) {
      var clean = line.replace(/^(4[).]?|телеграм[:\s]*)/i, "").trim();
      clean = clean.replace(/^(https?:\/\/)?t\.me\//i, "").replace(/^@/, "").trim();
      if (clean.length >= 3) {
        return "@" + clean;
      }
    }
  }
  return "";
}

function extractModelName(formText) {
  if (!formText) return "Модель";
  var lines = formText.split("\n");
  for (var i = 0; i < lines.length; i++) {
    var l = lines[i].trim();
    if (l.length > 0 && l.toLowerCase().indexOf("позиция") === -1 && l.toLowerCase().indexOf("собес") === -1) {
      var clean = l.replace(/^\d+[).]?\s*/, "")
        .replace(/^(имя|фио|модель)[:\s]*/i, "")
        .trim();
      if (clean.length > 0) {
        return clean;
      }
    }
  }
  return "Модель";
}

function extractModelPhone(formText) {
  if (!formText) return "";
  var lines = formText.split("\n");

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    var lower = line.toLowerCase();
    if (lower.indexOf("номер") !== -1 || lower.indexOf("телефон") !== -1 || line.indexOf("3)") === 0) {
      var clean = line.replace(/^\d+[).]?\s*/, "")
        .replace(/^(номер|телефон|телефон модели)[:\s]*/i, "")
        .trim();
      var phoneMatch = clean.match(/(\+?\d[0-9\s\-()]{3,}\d)/);
      if (phoneMatch) return phoneMatch[0].trim();
      if (clean.length >= 1) return clean;
    }
  }

  for (var j = 0; j < lines.length; j++) {
    var lText = lines[j].trim();
    var cleanText = lText.replace(/^\d+[).]?\s*/, "")
      .replace(/^(номер|телефон|телефон модели)[:\s]*/i, "")
      .trim();
    var match = cleanText.match(/(\+?\d[0-9\s\-()]{3,}\d)/);
    if (match) {
      return match[0].trim();
    }
  }
  return "";
}

String.prototype.strip = function () {
  return this.replace(/^\s+|\s+$/g, '');
};
