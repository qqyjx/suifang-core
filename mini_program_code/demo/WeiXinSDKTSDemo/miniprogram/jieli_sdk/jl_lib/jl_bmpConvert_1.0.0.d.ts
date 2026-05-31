/* 杰理bmp转换表盘资源库 version 1.0.0 */
/**
 * @param type 0: BR23图像转换算法 - RGB,1:BR28图像转换算法 - RGB,2:BR28图像转换算法 - ARGB,3: BR28图像转换算法 - RGB & 不打包封装,4:BR28图像转换算法 - ARGB & 不打包封装
 *
*/
declare function bmpConvert(type: number, filebuf: Uint8Array, width: number, height: number): Uint8Array | undefined;

export { bmpConvert };
/* follow me on Github! */
