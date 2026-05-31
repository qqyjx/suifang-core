export const formatTime = (date: Date) => {
  const year = date.getFullYear()
  const month = date.getMonth() + 1
  const day = date.getDate()
  const hour = date.getHours()
  const minute = date.getMinutes()
  const second = date.getSeconds()

  return (
    [year, month, day].map(formatNumber).join('/') +
    ' ' +
    [hour, minute, second].map(formatNumber).join(':')
  )
}

const formatNumber = (n: number) => {
  const s = n.toString()
  return s[1] ? s : '0' + s
}

export const incrementMacAddress = function (macAddress: any) {
  // 将mac地址分割为数组
  let macArray = macAddress.split(':');
  
  // 获取最后一组的值并加1
  let lastGroup = parseInt(macArray[5], 16);
  lastGroup++;
  
  // 将新的值转换成十六进制，并添加到数组中
  macArray[5] = lastGroup.toString(16).padStart(2, '0').toUpperCase();
  
  // 将数组重新组合成新的mac地址，并以大写形式返回
  let newMacAddress = macArray.join(':').toUpperCase();
  
  return newMacAddress;
}

// 监听蓝牙返回数据转十六进制
export const ab2hex = function (bufferData:any) {
  const hexArr = Array.prototype.map.call(
    new Uint8Array(bufferData),
    function (bit) {
      return ('00' + bit.toString(16)).slice(-2)
    }
  )
  return hexArr;
}