const TAG = "杰理-OTA-App"
function formatLog(msg: string) {
  let date = new Date()
  const year = date.getFullYear()
  const month = date.getMonth() + 1
  const day = date.getDate()
  const hour = date.getHours()
  const minute = date.getMinutes()
  const second = date.getSeconds()
  const mill = date.getMilliseconds()
  let timeString = `${[year, month, day].map(formatNumber).join('/')} ${[hour, minute, second, mill].map(formatNumber).join(':')}`
  return timeString + ":" + TAG + "-->" + msg;
}
const formatNumber = (n: any) => {
  n = n.toString()
  return n[1] ? n : `0${n}`
}
var logGrade: number = 1;
var logger: Logger | undefined;
export interface Logger {
  logv: (log: string) => void
  logd: (log: string) => void
  logi: (log: string) => void
  logw: (log: string) => void
  loge: (log: string) => void
}
export function setLogGrade(grade: number) {
  logGrade = grade
}
export function setLogger(log: Logger) {
  logger = log
}
var tagEnableMap = new Map<string, boolean>()
export function setTagEnable(tag: string, enable: boolean) {
  tagEnableMap.set(tag, enable)
}
function getTagEnable(tag: string) {
  let enable = tagEnableMap.get(tag)
  return enable!=undefined ? enable : true //默认使能
}
export function logv(msg: string, tag?: string) {
  if (tag && !getTagEnable(tag)) {//tag非undefined，且tag使能为false
    return
  }
  if (logGrade <= 1) {
    if (logger != undefined) {
      logger.logv(formatLog(msg));
    }
  }
}
export function logd(msg: string, tag?: string) {
  if (tag && !getTagEnable(tag)) {//tag非undefined，且tag使能为false
    return
  }
  if (logGrade <= 2) {
    if (logger != undefined) {
      logger.logd(formatLog(msg));
    }
  }
}
export function logi(msg: string, tag?: string) {
  if (tag && !getTagEnable(tag)) {//tag非undefined，且tag使能为false
    return
  }
  if (logGrade <= 3) {
    if (logger != undefined) {
      logger.logi(formatLog(msg));
    }
  }
}
export function logw(msg: string, tag?: string) {
  if (tag && !getTagEnable(tag)) {//tag非undefined，且tag使能为false
    return
  }
  if (logGrade <= 4) {
    if (logger != undefined) {
      logger.logw(formatLog(msg));
    }
  }
}
export function loge(msg: string, tag?: string) {
  if (tag && !getTagEnable(tag)) {//tag非undefined，且tag使能为false
    return
  }
  if (logGrade <= 5) {
    if (logger != undefined) {
      logger.loge(formatLog(msg));
    }
  }
}

/** arraybuffer 转字符串*/
export function ab2hex(buffer: ArrayBuffer) {
  const hexArr = Array.prototype.map.call(
    new Uint8Array(buffer),
    function (bit) {
      return ('00' + bit.toString(16)).slice(-2)
    }
  )
  return hexArr.join('')
}

/** 16进制数据转蓝牙地址 */
export function hexDataCovetToAddress(dataArray: Uint8Array) {
  let address = ""
  for (let index = 0; index < dataArray.length; index++) {
    const element = dataArray[index];
    address += ('00' + element.toString(16)).slice(-2)
    if (index != dataArray.length - 1) {
      address += ":"
    }
  }
  return address.toUpperCase()
}
