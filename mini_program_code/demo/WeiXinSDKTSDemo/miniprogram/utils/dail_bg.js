export class StreamController {  
  constructor(items, chunkSize) {  
    this.queue = [...items]; // 复制数组到队列中  
    this.chunkSize = chunkSize; // 每次发送的数组元素数量  
    this.status = false; // 控制发送的状态  
    this.sending = false; // 是否正在发送数据的标志  
  }  
  
  // 发送数据的函数  
  async sendChunk(chunk) {  
    // 这里发送数据的过程，使用Promise和setTimeout  
    return new Promise(resolve => {  
      // setTimeout(() => {  
        console.log('Sending chunk:', chunk);  
        // 假设发送完成后执行一些操作，比如更新UI或处理响应  
        resolve(); // 发送完成  
      // }, 50); // 假设每次发送需要1秒  
    });  
  }  
  
  // 开始发送数据的函数  
  async startSending() {  
    if (this.sending) return; // 如果已经在发送，则直接返回  
    this.sending = true;  
  
    while (this.queue.length > 0 && this.status) {  
      const chunk = this.queue.splice(0, this.chunkSize); // 取出队列中的一部分数据  
      await this.sendChunk(chunk); // 发送数据  
    }  
  
    this.sending = false; // 发送完成后设置标志为false  
  }  
  
  // 更新发送状态  
  updateStatus(newStatus) {  
    this.status = newStatus;  
    if (this.status && this.queue.length > 0 && !this.sending) {  
      this.startSending(); // 如果状态为真且队列中有数据且未发送，则开始发送  
    }  
  }  
}  
  
// // 使用示例  
// const items = Array.from({ length: 10000 }, (_, index) => `Item ${index + 1}`); // 生成10000个元素的数组  
// const controller = new StreamController(items, 20); // 创建一个流控制器，每次发送20个元素  
  
// // 初始状态为假，不发送数据  
// console.log('Initial status is false, not sending data.');  
  
// // 稍后，更改状态为true，开始发送数据  
// setTimeout(() => {  
//   console.log('Status changed to true, starting to send data.');  
//   controller.updateStatus(true);  
// }, 2000); // 假设2秒后开始发送  
  
// // 再稍后，更改状态为假，停止发送数据  
// setTimeout(() => {  
//   console.log('Status changed to false, stopping to send data.');  
//   controller.updateStatus(false);  
// }, 4000); // 假设4秒后停止发送  
  
// 再稍后，再次更改状态为true，继续发送剩余的数据（如果有的话）  
// setTimeout(() => {  
//   console.log('Status changed to true again, resuming to send remaining data.');  
//   controller.updateStatus(true);  
// }, 6000); // 假设6秒后重新开始发送